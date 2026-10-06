"""Split node_results' single ``result_json`` blob into per-field columns.

One-time migration for databases written before the split: every row carried
its whole record (status, value, error, provenance, …) as one zlib'd blob, so
every read inflated a payload that is mostly provenance (1.53 GB of the live
table) — including the readers that never look at a provenance tree.

This converts each row in place and then DROPS the legacy column, so the
database matches the fresh schema (which never creates it). The app reads
both shapes meanwhile, so a half-converted database stays usable and a
re-run resumes where it stopped.

Dry-run by default; anything that writes requires ``HOUSES_SCRIPTS_MAY_WRITE=1``
(the settings-write guard) plus ``--apply``. Run against a COPY first.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sqlite3
import sys
import zlib

import dag.persistence as per

NO_WRITE_MARKER = "no-write:"
"""Printed when this migration has nothing to write (tools/deploy/migrations.list).

The runner's rule is "an --apply run leaves a backup behind"; a database that is
already split writes nothing, so it states that instead of leaving a 600 MB copy
to prove a no-op."""

DB_PATH = "data/houses.db"



@dataclasses.dataclass(frozen=True)
class MigrationResult:
    """Outcome of one apply pass: whether it succeeded and how many rows it
    actually rewrote (the real count, not a post-apply re-scan)."""

    ok: bool
    applied: int
    dropped_column: bool


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(node_results)")}




_CHUNK_ROWS = 2_000
"""Rows per read and per commit. Bounding BOTH is what keeps the migration
inside the box's memory: the table's blobs are ~330 MB in total, so reading
them all at once (or holding one transaction over them) is an OOM, not a
slow path — the person-id migration learned this the hard way on 2026-09-23."""


# hoisting "what one item contributes" would put the whole table back in memory, the exact OOM this avoids
def convert_all(conn: sqlite3.Connection, pending_sql: str) -> int | None:
    """Convert every pending row; the count, or None on the first unreadable row.

    Streams in chunks and commits in bounded batches. Both bounds are the
    lesson from the person-id migration (2026-09-23: a dry-run that read the
    whole table — and an apply with one 860k-row transaction — OOM-killed the
    box): 250k rows of blobs is ~330 MB in Python objects, and a single
    transaction's journal is worse.
    """
    applied = 0
    # The columns this database has, hoisted: the per-row write derives its
    # column list from the shape owner's own mapping (below), so there is no
    # list here to drift.
    present = _columns(conn)
    cursor = conn.execute(pending_sql)
    # lucidlint: ignore loop-hoist the loop IS the streaming step (read a chunk, convert, commit) —
    # hoisting "what one item contributes" would put the table back in memory, the OOM this avoids
    while True:
        chunk = cursor.fetchmany(_CHUNK_ROWS)
        if not chunk:
            break
        # lucidlint: ignore loop-hoist per-row work IS the conversion; there is no collection to build
        for row_id, raw in chunk:
            if not convert_row(conn, row_id, raw, present):
                conn.commit()
                return None
            applied += 1
        conn.commit()
        print(f"  converted {applied} rows…", flush=True)
    return applied


def count_pending(conn: sqlite3.Connection) -> int:
    """Rows still holding a legacy blob (0 once the column is gone)."""
    if "result_json" not in _columns(conn):
        return 0
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM node_results WHERE result_json IS NOT NULL AND result_json != ''"
        ).fetchone()[0]
    )


def ensure_split_columns(conn: sqlite3.Connection) -> None:
    """Add any missing split column — the schema owner's own DDL, so the
    migration and the app cannot disagree about the columns."""
    per.ensure_split_columns(conn)


def convert_row(conn: sqlite3.Connection, row_id: int, raw: bytes | str, present: set[str]) -> bool:
    """Convert one legacy row into its split columns; False if unreadable.

    The record is decoded with the storage shape's own reader and written with
    its own column mapping (``dag.persistence``) — one owner, so the migration
    cannot drift from what the app stores and reads.
    """
    try:
        record = json.loads(per.decompress_result(raw))
    except (ValueError, zlib.error) as exc:
        print(f"row {row_id}: unreadable record ({exc}) — left for a human", file=sys.stderr)
        return False
    if not isinstance(record, dict):
        # JSON that parses but is not a record (a bare string, array or
        # number). The column mapping reads keys, and `"value" in "some value"`
        # is a substring test, not a lookup — so report it like the unreadable
        # case rather than misreading or crashing on it.
        print(
            f"row {row_id}: blob is {type(record).__name__}, not a record — left for a human",
            file=sys.stderr,
        )
        return False
    values = per.record_columns(record)
    # the blob column is deliberately excluded: it holds the legacy original,
    # intact until the drop, so a half-converted database still reads with the
    # previous artifact.
    columns = values.writable_columns(present, include_blob=False)
    assignments = ", ".join(f"{column}=?" for column in columns)
    conn.execute(
        f"UPDATE node_results SET {assignments} WHERE id=?",
        (*values.for_columns(columns), row_id),
    )
    return True


def drop_legacy_column(conn: sqlite3.Connection) -> bool:
    """Drop ``result_json`` once no row needs it; False when SQLite refuses."""
    if "result_json" not in _columns(conn):
        return False
    try:
        conn.execute("ALTER TABLE node_results DROP COLUMN result_json")
    except sqlite3.OperationalError as exc:
        print(
            f"cannot drop result_json ({exc}) — converted rows are correct; the empty column remains",
            file=sys.stderr,
        )
        return False
    conn.commit()
    return True


def apply_migration(
    conn: sqlite3.Connection,
    *,
    backup_path: str | None,
    verify: bool,
) -> MigrationResult:
    """Convert every legacy row, then drop the blob column."""
    ensure_split_columns(conn)
    if backup_path:
        conn.backup(sqlite3.connect(backup_path))
        print(f"backup written: {backup_path}")

    applied = convert_all(
        conn,
        "SELECT id, result_json FROM node_results"
        " WHERE result_json IS NOT NULL AND result_json != ''",
    )
    if applied is None:
        return MigrationResult(ok=False, applied=0, dropped_column=False)
    dropped = drop_legacy_column(conn)

    if verify:
        leftover = count_pending(conn)
        legacy_column = "result_json" in _columns(conn)
        if leftover or legacy_column:
            print(
                f"VERIFY FAILED: {leftover} legacy rows, legacy column present: {legacy_column}",
                file=sys.stderr,
            )
            return MigrationResult(ok=False, applied=applied, dropped_column=dropped)
        print("verify: no legacy rows, no legacy column")
    return MigrationResult(ok=True, applied=applied, dropped_column=dropped)


@dataclasses.dataclass(frozen=True)
class _Outcome:
    """What the run decided: an applied result (or None for a dry run) and the exit code."""

    result: MigrationResult | None
    exit_code: int


def _report(conn: sqlite3.Connection, options: RunOptions) -> _Outcome:
    """The dry-run path, or the applied result; the int is the process exit code."""
    pending = count_pending(conn)
    if pending == 0 and "result_json" not in _columns(conn):
        # The runner requires a pre-write backup unless the apply says it wrote
        # nothing: this is that statement (contract in tools/deploy/migrations.list).
        print(f"{NO_WRITE_MARKER} already split: no legacy column, no legacy rows")
        return _Outcome(result=None, exit_code=0)
    if pending == 0:
        print("no rows to convert; the legacy column is empty (drop it with --apply)")
    if not options.apply:
        print(f"rows carrying a legacy blob: {pending} (dry-run)")
        return _Outcome(result=None, exit_code=0)
    # The write guard fires HERE, where a write is actually about to happen:
    # a dry run and a database with nothing to convert never write.
    if os.environ.get("HOUSES_SCRIPTS_MAY_WRITE") != "1":
        print("Refusing to write: HOUSES_SCRIPTS_MAY_WRITE=1 is required (house rule).", file=sys.stderr)
        return _Outcome(result=None, exit_code=2)
    result = apply_migration(
        conn,
        backup_path=(
            (options.backup_path or f"{options.db}.pre-{os.path.basename(__file__)}")
            if options.backup
            else None
        ),
        verify=options.verify,
    )
    return _Outcome(result=result, exit_code=0 if result.ok else 1)


@dataclasses.dataclass(frozen=True)
class RunOptions:
    """What one run is asked to do (the CLI's parsed flags)."""

    db: str
    apply: bool
    backup: bool
    backup_path: str | None
    verify: bool


def run(options: RunOptions) -> int:
    """Convert one database; the exit code IS the verdict."""
    conn = sqlite3.connect(options.db)
    conn.row_factory = sqlite3.Row
    try:
        if not conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='node_results'"
        ).fetchone():
            print("no node_results table — nothing to do")
            return 0
        outcome = _report(conn, options)
    finally:
        conn.close()

    if outcome.result is None:
        return outcome.exit_code
    print(
        f"node_results rows converted: {outcome.result.applied}"
        f" | legacy column dropped: {outcome.result.dropped_column}"
    )
    return outcome.exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the changes (dry-run default)")
    parser.add_argument("--backup", action="store_true", help="sqlite backup before writing")
    parser.add_argument(
        "--backup-path", default=None, help="where --backup writes (default: <db>.pre-<script>)"
    )
    parser.add_argument("--verify", action="store_true", help="re-scan after apply: zero legacy rows")
    parser.add_argument("--db", default=DB_PATH, help="sqlite path (default: data/houses.db)")
    args = parser.parse_args()
    return run(
        RunOptions(
            db=args.db,
            apply=args.apply,
            backup=args.backup,
            backup_path=args.backup_path,
            verify=args.verify,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
