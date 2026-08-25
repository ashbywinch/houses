"""Inspect node data from the real DB — helps find format mismatches.

Usage:
    uv run python scripts/debug_dag.py <rid>          # inspect one property
    uv run python scripts/debug_dag.py --list          # list known properties
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from houses.settings import settings

logger = logging.getLogger(__name__)


def _json_or_raw(value: str) -> object:
    """Parse a stored JSON value; fall back to a truncated raw string."""
    try:
        return json.loads(value)
    except ValueError as e:
        logger.debug("corrupt stored JSON (showing raw text): %s", e)
        return value[:60]


def _get_conn():
    db = Path(settings.sqlite_path)
    if not db.exists():
        print(f"Database not found at {db}")
        sys.exit(1)
    conn = sqlite3.connect(str(db), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def list_properties():
    conn = _get_conn()
    rows = conn.execute("SELECT DISTINCT property_id FROM source_values ORDER BY property_id").fetchall()
    print(f"Properties ({len(rows)}):")
    for r in rows:
        rid = r["property_id"]
        row = conn.execute(
            "SELECT value FROM source_values WHERE property_id=? AND node_id=?",
            (rid, f"{rid}/rightmove_address"),
        ).fetchone()
        addr = json.loads(row["value"]) if row else "(?)"
        print(f"  {rid}  {addr}")
    conn.close()


def inspect_rid(rid: str):
    conn = _get_conn()

    # Persisted node results
    rows = conn.execute(
        "SELECT node_id, result_json FROM node_results WHERE node_id LIKE ? ORDER BY node_id",
        (f"{rid}/%",),
    ).fetchall()

    if not rows:
        print(f"No results for {rid!r}")
        conn.close()
        return

    nodes: dict = {}
    for r in rows:
        nid = r["node_id"]
        if nid not in nodes:
            try:
                nodes[nid] = json.loads(r["result_json"])
            except ValueError as e:
                logger.debug("corrupt result_json for %s (shown as _corrupt): %s", nid, e)
                nodes[nid] = {"_corrupt": str(r["result_json"][:200])}
                continue
        data = nodes[nid]
        short = nid.replace(f"{rid}/", "", 1)
        status = data.get("status", "?")
        # lucidlint: ignore boolean-arg False is dict.get's default value, not a named flag — no swap risk
        s = data.get("succeeded", False)
        # lucidlint: ignore boolean-arg False is dict.get's default value, not a named flag — no swap risk
        p = data.get("pending", False)
        # lucidlint: ignore boolean-arg False is dict.get's default value, not a named flag — no swap risk
        i = data.get("impossible", False)
        val = data.get("value")
        error = data.get("error", "")

        parts = [f"  {short:45s} [{status:12s}]  s={s} p={p} i={i}"]
        if error:
            parts[0] += f"  err={error}"

        if isinstance(val, dict):
            keys = list(val.keys())
            dur = val.get("duration")
            dur_str = json.dumps(dur) if dur else "MISSING"
            parts.append(f"    keys={keys}  duration={dur_str}")
        elif val is None:
            parts.append("    value=None")
        else:
            parts.append(f"    value={type(val).__name__}:{str(val)[:80]}")

        print("\n".join(parts))

    # Source values
    sv = conn.execute(
        "SELECT node_id, value, source FROM source_values WHERE property_id=?",
        (rid,),
    ).fetchall()
    if sv:
        print("\n  --- source_values ---")
        for row in sv:
            short = row["node_id"].replace(f"{rid}/", "", 1)
            v = _json_or_raw(row["value"])
            print(f"    {short:30s} src={row['source']:20s} val={str(v)[:80]}")

    conn.close()


def _print_derived_values(conn, rid):
    dv = conn.execute(
        "SELECT node_id, value, source, error FROM derived_values WHERE property_id=?",
        (rid,),
    ).fetchall()
    if dv:
        print("\n  --- derived_values ---")
        for row in dv:
            short = row["node_id"].replace(f"{rid}/", "", 1)
            v = _json_or_raw(row["value"])
            print(f"    {short:30s} src={row['source']:20s} err={row['error']} val={str(v)[:80]}")

def main():
    args = sys.argv[1:]
    if not args or "-h" in args or "--help" in args:
        print(__doc__)
        return
    if "--list" in args:
        list_properties()
        return
    for rid in args:
        if rid.startswith("--"):
            continue
        inspect_rid(rid)


if __name__ == "__main__":
    main()
