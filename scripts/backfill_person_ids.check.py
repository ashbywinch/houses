#!/usr/bin/env python3
"""backfill_person_ids.check.py — the paired check for backfill_person_ids.py.

Invoked by tools/deploy/run_migrations.py as

    <side .venv python> scripts/backfill_person_ids.check.py --db <db>

Opens the database READ-ONLY and exits 0 iff the migration's effect is
complete:

  1. every person in the latest ``persons`` row carries a ``person_id``;
  2. no persisted row still keys a PERSON by name — not in ``node_id``, not
     in a ``dep_timestamps`` key, not in a ``works_estimates`` money
     payload (the legacy JSON-string shape included).

The scan is written against the post-state, not against the migration's
code: it never imports the script that just wrote, so it cannot agree with
it by construction. A separate process and an independent statement of the
invariant — that is the whole point of a paired check.

Read-only: no writes of any kind. Exit codes: 0 complete; 1 incomplete;
2 the database cannot be scanned at all.

Row shape the scan mirrors (post-migration): ``{rid}/{person}/{label}``
with the person segment position 2 — the only name-bearing slot.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sqlite3
import sys
import zlib
from pathlib import Path

from houses.model.domain import slugify

DESCRIPTION = "Verify the person-id migration completed (read-only paired check)."
PERSON_SEGMENT_INDEX = 1  # {rid}/{person}/{label}/{step} — position 2
MIN_NODE_ID_PARTS = 3  # below this there is no person segment to key
WORKS_ESTIMATES_SUFFIX = "/works_estimates"
PERSONS_NODE_ID = "persons"
EXIT_COMPLETE = 0
EXIT_INCOMPLETE = 1
EXIT_UNSCANNABLE = 2
# The one read-only scan both the versioned-row fold and the works payload need.
UNREADABLE = (zlib.error, ValueError, AttributeError)


@dataclasses.dataclass(frozen=True)
class Leftovers:
    """What one row (or the whole scan) still has to migrate."""

    persons_without_id: int = 0
    node_ids: int = 0
    dep_keys: int = 0
    works_keys: int = 0
    unreadable: int = 0

    def __add__(self, other: Leftovers) -> Leftovers:
        return Leftovers(
            self.persons_without_id + other.persons_without_id,
            self.node_ids + other.node_ids,
            self.dep_keys + other.dep_keys,
            self.works_keys + other.works_keys,
            self.unreadable + other.unreadable,
        )

    @property
    def incomplete(self) -> bool:
        return bool(
            self.persons_without_id
            or self.node_ids
            or self.dep_keys
            or self.works_keys
            or self.unreadable
        )

    def reason(self) -> str:
        return (
            f"check INCOMPLETE: {self.persons_without_id} person(s) without person_id, "
            f"{self.node_ids} node id(s), {self.dep_keys} dep key(s), "
            f"{self.works_keys} works key(s), {self.unreadable} unreadable row(s) "
            "still keyed by name"
        )


@dataclasses.dataclass(frozen=True)
class Persons:
    """The newest ``persons`` row, decoded."""

    payload: dict

    @property
    def people(self) -> list:
        value = self.payload.get("value")
        return value if isinstance(value, list) else []


def _person_keys(persons_value) -> set[str]:
    """Every spelling of a person this DB may still be keyed by: the display
    name and its slug — the app wrote slug-segment ids before the cutover."""
    keys = set()
    for person in persons_value or []:
        name = person.get("name") if isinstance(person, dict) else getattr(person, "name", None)
        if name:
            keys.update((str(name), slugify(str(name))))
    return keys


def _lacks_id(person) -> int:
    """1 when this person still has no stable id."""
    if isinstance(person, dict):
        name, person_id = person.get("name"), person.get("person_id")
    else:
        name, person_id = getattr(person, "name", None), getattr(person, "person_id", "")
    return 1 if name and not person_id else 0


def _has_person_segment(node_id: str, keys: set[str]) -> bool:
    """True when the person segment still names a person (never a whole-string
    search: a POI or step literally called ``Dad`` is not a person segment)."""
    parts = node_id.split("/")
    if len(parts) < MIN_NODE_ID_PARTS or not parts[0]:
        return False
    return parts[PERSON_SEGMENT_INDEX] in keys


def _unwrapped_value(blob: bytes):
    """A row's stored ``value``, with the legacy JSON-string shape unwrapped."""
    payload = json.loads(zlib.decompress(blob).decode())
    value = payload.get("value")
    return json.loads(value) if isinstance(value, str) else value


def _works_leftovers(blob: bytes, keys: set[str]) -> Leftovers:
    """What one ``works_estimates`` payload still keys by person name."""
    if not blob:
        return Leftovers()
    try:
        value = _unwrapped_value(blob)
    except UNREADABLE:
        return Leftovers(unreadable=1)
    if not isinstance(value, dict):
        return Leftovers()
    return Leftovers(works_keys=sum(1 for key in value if key in keys))


def _row_leftovers(row: sqlite3.Row, keys: set[str]) -> Leftovers:
    """What one ``node_results`` row still keys by person name."""
    node_id = row["node_id"] or ""
    try:
        dep_keys = sum(
            1 for key in json.loads(row["dep_timestamps"] or "{}") if _has_person_segment(key, keys)
        )
    except json.JSONDecodeError:
        return Leftovers(node_ids=1 if _has_person_segment(node_id, keys) else 0, unreadable=1)
    works = (
        _works_leftovers(row["result_json"], keys)
        if node_id.endswith(WORKS_ESTIMATES_SUFFIX)
        else Leftovers()
    )
    return works + Leftovers(
        node_ids=1 if _has_person_segment(node_id, keys) else 0,
        dep_keys=dep_keys,
    )


def _latest_persons(conn: sqlite3.Connection) -> Persons | None:
    """The newest persons row, decoded; None when the DB has none."""
    row = conn.execute(
        "SELECT result_json FROM node_results WHERE node_id=?"
        " ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (PERSONS_NODE_ID,),
    ).fetchone()
    if row is None:
        return None
    return Persons(json.loads(zlib.decompress(row["result_json"]).decode()))


def _scan(conn: sqlite3.Connection, keys: set[str]) -> Leftovers:
    """Fold every row's leftovers — streaming, never materializing the table."""
    totals = Leftovers()
    for row in conn.execute("SELECT node_id, dep_timestamps, result_json FROM node_results"):
        totals = totals + _row_leftovers(row, keys)
    return totals


def main() -> int:
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--db", required=True, help="sqlite database the migration was applied to")
    args = parser.parse_args()

    db = Path(args.db).resolve()
    if not db.is_file():
        print(f"check: database not found: {db}", file=sys.stderr)
        return EXIT_UNSCANNABLE
    # mode=ro: the check must not be able to write, even by accident.
    conn = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        found = _latest_persons(conn)
        if found is None:
            print("check INCOMPLETE: no persons row — the migration has not run", file=sys.stderr)
            return EXIT_INCOMPLETE
        keys = _person_keys(found.people)
        leftovers = _scan(conn, keys) + Leftovers(
            persons_without_id=sum(_lacks_id(person) for person in found.people)
        )
    except sqlite3.Error as exc:
        print(f"check: cannot scan {db} ({exc})", file=sys.stderr)
        return EXIT_UNSCANNABLE
    finally:
        conn.close()

    if leftovers.incomplete:
        print(leftovers.reason(), file=sys.stderr)
        return EXIT_INCOMPLETE
    print(f"check ok: {len(keys)} name spelling(s) checked, 0 rows still keyed by name")
    return EXIT_COMPLETE


if __name__ == "__main__":
    raise SystemExit(main())
