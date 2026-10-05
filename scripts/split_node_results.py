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

DB_PATH = "data/houses.db"

_ZLIB_LEVEL = 6
"""Matches dag.persistence._ZLIB_LEVEL: 6 vs 9 trades ~2% ratio for ~4x
faster compress, and the provenance trees compress ~25x either way."""

_SPLIT_COLUMN_SQL: tuple[tuple[str, str], ...] = (
    ("status", "TEXT"),
    ("value_json", "TEXT"),
    ("error", "TEXT"),
    ("error_detail_json", "TEXT"),
    ("source_url", "TEXT"),
    ("source_label", "TEXT"),
    ("provenance_z", "BLOB"),
    ("extra_json", "TEXT"),
)

_SPLIT_COLUMNS: tuple[str, ...] = tuple(column for column, _ in _SPLIT_COLUMN_SQL)

_PLAIN_KEYS: tuple[str, ...] = ("status", "error", "source_url", "source_label")
_MAPPED_KEYS: frozenset[str] = frozenset({*_PLAIN_KEYS, "value", "error_detail", "provenance"})


@dataclasses.dataclass(frozen=True)
class MigrationResult:
    """Outcome of one apply pass: whether it succeeded and how many rows it
    actually rewrote (the real count, not a post-apply re-scan)."""

    ok: bool
    applied: int
    dropped_column: bool


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(node_results)")}


# lucidlint: ignore record-shape the legacy record IS the stored wire shape (keys vary per node type); this
# migration reads it as data and writes the split columns — coding-standards.md
def _decode(raw: object) -> dict:
    """A legacy ``result_json`` value as the record it holds (zlib or plain)."""
    if isinstance(raw, (bytes, memoryview)):
        data = bytes(raw)
        if data[:1] == b"\x78":
            return json.loads(zlib.decompress(data).decode("utf-8"))
        return json.loads(data.decode("utf-8"))
    return json.loads(str(raw))


@dataclasses.dataclass(frozen=True)
class _SplitValues:
    """One record's fields as the split columns take them."""

    status: str | None
    value_json: str | None
    error: str | None
    error_detail_json: str | None
    source_url: str | None
    source_label: str | None
    provenance_z: bytes | None
    extra_json: str | None

    def for_columns(self, columns: tuple[str, ...]) -> tuple[object, ...]:
        """The values in the caller's column order."""
        return tuple(getattr(self, column) for column in columns)


# lucidlint: ignore record-shape the legacy record IS the stored wire shape (keys vary per node type); it is
# read as data and written back as columns — coding-standards.md
def _split_values(record: dict) -> _SplitValues:
    """The record's fields as the split column values (provenance compresses)."""
    provenance = record.get("provenance")
    extra = {key: value for key, value in record.items() if key not in _MAPPED_KEYS}
    return _SplitValues(
        status=record.get("status"),
        value_json=json.dumps(record["value"]) if "value" in record else None,
        error=record.get("error"),
        error_detail_json=(
            json.dumps(record["error_detail"]) if record.get("error_detail") is not None else None
        ),
        source_url=record.get("source_url"),
        source_label=record.get("source_label"),
        provenance_z=(
            zlib.compress(json.dumps(provenance).encode("utf-8"), _ZLIB_LEVEL)
            if provenance is not None
            else None
        ),
        extra_json=json.dumps(extra) if extra else None,
    )


_COMMIT_EVERY = 10_000
"""Bound the journal: 250k converted rows in one transaction means a huge WAL
and a rollback that has to undo all of it (the 2026-09-24 lesson in the
person-id migration: a single 860k-row transaction OOM-killed the box)."""


def convert_all(conn: sqlite3.Connection, rows: list) -> int | None:
    """Convert every row; the count converted, or None on the first unreadable row.

    Commits in bounded batches and reports progress — a 250k-row conversion on
    a box disk takes minutes, and silence reads as a hang.
    """
    applied = 0
    for row_id, raw in rows:
        if not convert_row(conn, row_id, raw):
            conn.commit()
            return None
        applied += 1
        if applied % _COMMIT_EVERY == 0:
            conn.commit()
            print(f"  converted {applied} rows…", flush=True)
    conn.commit()
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
    """Add any missing split column (idempotent ALTERs)."""
    existing = _columns(conn)
    for column, column_type in _SPLIT_COLUMN_SQL:
        if column not in existing:
            conn.execute(f"ALTER TABLE node_results ADD COLUMN {column} {column_type}")
    conn.commit()


def convert_row(conn: sqlite3.Connection, row_id: int, raw: object) -> bool:
    """Convert one legacy row into its split columns; False if unreadable."""
    try:
        record = _decode(raw)
    except (ValueError, zlib.error) as exc:
        print(f"row {row_id}: unreadable record ({exc}) — left for a human", file=sys.stderr)
        return False
    values = _split_values(record)
    assignments = ", ".join(f"{column}=?" for column in _SPLIT_COLUMNS)
    conn.execute(
        f"UPDATE node_results SET {assignments} WHERE id=?",
        (*values.for_columns(_SPLIT_COLUMNS), row_id),
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
        conn.execute(
            "SELECT id, result_json FROM node_results WHERE result_json IS NOT NULL AND result_json != ''"
        ).fetchall(),
    )
    conn.commit()
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
        print("already split: no legacy column, no legacy rows")
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
