"""Backfill person_id and re-key name-keyed DAG rows (one-time migration).

Seeds: ``Person.person_id`` — stable numeric ids assigned once,
incrementally (defaults are 1..N; the backfill takes max(existing
numeric ids)+1 for every person still missing one, in list order).
After this runs, renames never move identity: attribution, pipelines,
and the session all key by the id.

The re-key remaps persisted name-keyed rows so existing properties
KEEP their computed attempts under the new id-keyed node ids — no
startup replan beyond what a normal deploy would do anyway:

- ``node_results.node_id``: ``{rid}/Simon/Pimlico/walk`` →
  ``{rid}/p_simon/Pimlico/walk``
- ``dep_timestamps`` JSON keys: same id remap
- ``result_json`` VALUE dicts of ``*/works_estimates`` rows: the
  per-person estimate keys name → id (the persisted user-input money a
  rename would otherwise orphan)

Dry-run by default; anything that writes requires
``HOUSES_SCRIPTS_MAY_WRITE=1`` (the settings-write guard) plus
``--apply``. Run against a COPY first.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import zlib
from collections.abc import Mapping
from datetime import UTC, datetime

from houses.model.domain import slugify

DB_PATH = "data/houses.db"

_NUMERIC_ID_RE = re.compile(r"^[0-9]+$")


def _rekey_node_id(node_id: str, mapping) -> str:
    """Re-key the PERSON segment of a node id — position 2, the only
    name-bearing slot in the format ``{rid}/{person}/{label}/{step}``.

    Never a whole-string replace: a POI label or step name that equals a
    person name (a destination literally called ``Dad``, a person named
    ``walk``) would otherwise be re-keyed too. Both the display name and
    its slug resolve — the running app may have written slug-segment ids
    between the code cutover and this migration.
    """
    parts = node_id.split("/")
    if len(parts) >= 3 and parts[0]:
        pid = mapping.get(parts[1])
        if pid is None:
            pid = next((mid for name, mid in mapping.items() if slugify(name) == parts[1]), None)
        if pid:
            parts[1] = pid
            return "/".join(parts)
    return node_id



def remap_row(node_id: str, dep_timestamps: Mapping[str, str], result_json_blob: bytes, mapping) -> tuple | None:
    """Apply the name→id mapping to one row.

    Returns the remapped (node_id, dep_timestamps_json, result_json_blob)
    or None when nothing changes. Pure — unit-testable without the DB.
    """
    new_node_id = _rekey_node_id(node_id, mapping)

    dep = {_rekey_node_id(k, mapping): v for k, v in dep_timestamps.items()}

    payload = None
    if node_id.endswith("/works_estimates") and result_json_blob:
        try:
            payload = json.loads(zlib.decompress(result_json_blob).decode())
            value = payload.get("value")
            if isinstance(value, str):
                # legacy sheet-migration rows store the dict as a JSON STRING
                nested = json.loads(value)
                if isinstance(nested, dict):
                    value = nested
                    payload["value"] = nested
            if isinstance(value, dict):
                # keys may be the display name (pre-migration) OR the slug
                # fallback (writes between the code cutover and this run)
                renamed = {_works_key(k, mapping): v for k, v in value.items()}
                if renamed != value:
                    payload["value"] = renamed
                else:
                    payload = None  # already remapped — nothing to write (idempotence)
            else:
                payload = None  # not a person-keyed dict — nothing to remap
        # lucidlint: ignore swallow a corrupt blob stays completely untouched —
        # malparse keeps the row bytes as-is
        except Exception:
            payload = None

    if node_id == new_node_id and dep == dict(dep_timestamps) and payload is None:
        return None
    return (
        new_node_id,
        json.dumps(dep),
        zlib.compress(json.dumps(payload).encode()) if payload is not None else result_json_blob,
    )


def _works_key(key: str, mapping) -> str:
    """Works-estimate key → person id: the display name or its slug both
    resolve (writes between the code cutover and this migration store
    the slug fallback — a rename must not orphan them)."""
    if key in mapping:
        return mapping[key]
    for name, pid in mapping.items():
        if slugify(name) == key:
            return pid
    return key
# lucidlint: ignore record-shape the mapping is a derived rename table feeding a pure
# string transform — not a wire record
def collect_mapping(persons_value) -> dict:
    """{display name → person_id}: existing ids are kept; everyone still
    missing one gets max(existing numeric ids)+1, assigned in list order —
    deterministic and collision-free (numeric ids never collide)."""
    mapping = {}
    existing = []
    for p in persons_value or []:
        if isinstance(p, dict):
            name = p.get("name")
            pid = p.get("person_id") or ""
            if name:
                mapping[name] = pid
                if _NUMERIC_ID_RE.match(pid):
                    existing.append(int(pid))
        else:
            name = getattr(p, "name", None)
            pid = getattr(p, "person_id", "") or ""
            if name:
                mapping[name] = pid
                if _NUMERIC_ID_RE.match(pid):
                    existing.append(int(pid))
    next_id = max(existing, default=0) + 1
    for name, pid in mapping.items():
        if not pid:
            mapping[name] = str(next_id)
            next_id += 1
    return mapping


# lucidlint: ignore latent-class one-shot migration threads one connection through IO steps by design —
# a class wrapper adds ceremony without a reuse axis; tests pin the transform
def rows_to_remap(conn, mapping):
    """Scan every row and produce the remapped (id, node_id, dep_timestamps, result_json) entries."""
    out = []
    for row in conn.execute("SELECT id, node_id, dep_timestamps, result_json FROM node_results").fetchall():
        dep = json.loads(row["dep_timestamps"] or "{}")
        remapped = remap_row(row["node_id"], dep, row["result_json"], mapping)
        if remapped is None:
            continue
        out.append((row["id"], *remapped))
    return out


def _read_persons(conn):
    """The latest persons row as (id, decoded dict); None when absent."""
    persons = conn.execute(
        "SELECT id, result_json FROM node_results WHERE node_id='persons'"
        " ORDER BY created_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    if persons is None:
        return None
    return persons["id"], json.loads(zlib.decompress(persons["result_json"]).decode())


def backfill_persons(conn, persons_row_id: int, data, mapping) -> None:
    """Write the stable person_id onto every person in the persons row."""
    for p in data.get("value", []):
        if isinstance(p, dict):
            name = p.get("name")
            if name and not p.get("person_id"):
                p["person_id"] = mapping[name]
        else:
            pid = mapping.get(getattr(p, "name", ""), "")  # defensive: stored rows are dicts
            if pid and not getattr(p, "person_id", ""):
                p.person_id = pid
    conn.execute(
        "UPDATE node_results SET result_json=? WHERE id=?",
        (zlib.compress(json.dumps(data).encode()), persons_row_id),
    )


def _mark_changed_sources(conn, persons_row_id: int) -> None:
    """Advance the freshness stamp (created_at) of the SOURCE rows whose
    content this migration rewrote: the persons row and every
    person-keyed works_estimates row. The persons bump targets the
    CURRENT row by id — never the whole history: stamping every
    version identically destroys the append-order the latest-row query
    relies on (and an old row can win the tie).

    created_at IS the DAG's data-staleness contract (derived_node._is_stale
    compares each dep row's _db_created_at against the stored
    dep_timestamps). Rewriting source content without advancing the stamp
    leaves consumers looking fresh while their inputs changed — exactly
    the boot-sweep blind spot this migration hit. Derived rows are NOT
    marked: their values didn't change; the sweep will re-price them
    through the sources' new stamps, and route planners (address-keyed)
    stay untouched.
    """
    now = datetime.now(UTC).isoformat()
    conn.execute("UPDATE node_results SET created_at=? WHERE id=?", (now, persons_row_id))
    for row in conn.execute(
        "SELECT id, result_json FROM node_results WHERE node_id LIKE '%/works_estimates'"
    ).fetchall():
        try:
            value = json.loads(zlib.decompress(row["result_json"]).decode()).get("value")
        except Exception:
            continue
        if isinstance(value, dict) and value:
            conn.execute("UPDATE node_results SET created_at=? WHERE id=?", (now, row["id"]))


# lucidlint: ignore long-param-list one-shot migration step — the signature IS its IO boundary;
# an Options object would be a facade over nothing
def apply_migration(conn, db_path: str, persons_id: int, data, mapping, remaps, *, use_backup: bool, verify: bool) -> bool:  # noqa: E501  # noqa: E501 split-style: ruff line-length on a call signature the engine splits at call sites
    """Write the transform, checkpoint first, and prove it exhausted the
    work. True on success; False means the run must fail loudly."""
    if use_backup:
        backup_path = db_path + ".pre-person-id-migration"
        conn.backup(sqlite3.connect(backup_path))
        print(f"backup written: {backup_path}")
    for idx, (row_id, node_id, dep_json, blob) in enumerate(remaps):
        conn.execute(
            "UPDATE node_results SET node_id=?, dep_timestamps=?, result_json=? WHERE id=?",
            (node_id, dep_json, blob, row_id),
        )
        if idx % 10000 == 9999:
            conn.commit()  # bound the journal: a single 860k-row transaction
            # needs a ~2.4 GB rollback journal — the original out-of-disk crash
            # (2026-09-18). Batched commits keep it bounded and make the
            # idempotent migration resumable after any interruption.
    _mark_changed_sources(conn, persons_id)
    backfill_persons(conn, persons_id, data, mapping)
    conn.commit()
    if verify:
        second_persons = _read_persons(conn)
        if second_persons is None:
            print("VERIFY FAILED: persons row missing after apply", file=sys.stderr)
            return False
        second = rows_to_remap(conn, collect_mapping(second_persons[1].get("value")))
        if second:
            print(f"VERIFY FAILED: {len(second)} rows still remappable", file=sys.stderr)
            return False
        print("verify: zero rows remappable after apply")
    return True

# lucidlint: ignore latent-class main is the thin CLI shell over the same one-shot IO steps (see rows_to_remap)
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the changes (dry-run default)")
    parser.add_argument("--backup", action="store_true", help="sqlite backup of the DB before writing (recommended)")
    parser.add_argument("--verify", action="store_true", help="re-scan after apply: must find zero remaps")
    parser.add_argument("--db", default=DB_PATH, help="sqlite path (default: data/houses.db)")
    args = parser.parse_args()

    if args.apply and os.environ.get("HOUSES_SCRIPTS_MAY_WRITE") != "1":
        print("Refusing to write: HOUSES_SCRIPTS_MAY_WRITE=1 is required (house rule).", file=sys.stderr)
        return 2

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    persons_values = _read_persons(conn)
    if persons_values is None:
        return 1
    persons, data = persons_values
    mapping = collect_mapping(data.get("value"))
    if not mapping:
        print("no persons found — nothing to do", file=sys.stderr)
        return 1
    remaps = rows_to_remap(conn, mapping)
    if args.apply:
        ok = apply_migration(
            conn,
            args.db,
            persons,
            data,
            mapping,
            remaps,
            use_backup=args.backup,
            verify=args.verify,
        )
        if not ok:
            return 2
    print(f"persons: {len(mapping)} | rows remapped: {len(remaps)} (applied)" if args.apply
          else f"persons: {len(mapping)} | rows remapped: {len(remaps)} (dry-run)")
    if remaps:
        print("sample node ids:", [r[1] for r in remaps[:3]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())