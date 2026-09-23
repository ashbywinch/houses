"""The generic migration runner (tools/deploy/run-migration.sh) contract.

Pins the release step's guarantees with the REAL migration script
(backfill_person_ids.py) against a temp DB: a plain run dry-runs then
applies (re-keying, writing the pre-migration backup, verifying), a
second run is a no-op, the space gate refuses below the threshold, a
ref missing its listed script fails loudly, and the explicit --dry-run
flag writes nothing.

The runner is the ONE deployment path for data migrations; a future
migration that works with these tests needs no new machinery.
"""
# lucidlint: ignore-file fakefs the code under test is a shell script
# executed via subprocess against a real sqlite file — the standard's own
# cited exemptions (subprocess interop, C-level IO like sqlite3), the same
# carve-out as test_works_equity_mortgage.
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import zlib

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RUNNER = os.path.join(REPO, "tools", "deploy", "run-migration.sh")
MIGRATE = os.path.join(REPO, "scripts", "backfill_person_ids.py")
PYTHON = sys.executable


SCHEMA = """
CREATE TABLE node_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT,
    result_json BLOB,
    dep_timestamps TEXT,
    created_at TEXT,
    code_version TEXT
);
"""


def _row(conn, node_id: str, payload) -> None:
    conn.execute(
        "INSERT INTO node_results (node_id, result_json, dep_timestamps, created_at, code_version) VALUES (?,?,?,?,?)",
        (node_id, zlib.compress(json.dumps(payload).encode()), "{}", "2026-01-01T00:00:00", "v9"),
    )


def _seed_db(path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    _row(conn, "persons", {"status": "succeeded", "value": [{"name": "Simon", "has_car": True}]})
    _row(conn, "111/Simon/Pimlico/walk", {"status": "succeeded", "value": "x"})
    conn.commit()
    conn.close()


def _node_ids(path) -> list[str]:
    conn = sqlite3.connect(path)
    rows = [r[0] for r in conn.execute("SELECT node_id FROM node_results ORDER BY id")]
    conn.close()
    return rows


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    full_env = {**os.environ, "HOUSES_ROOT": "/tmp", "HOUSES_LOG_DIR": "/tmp"}
    if env:
        full_env.update(env)
    return subprocess.run([RUNNER, *args], capture_output=True, text=True, env=full_env)


def test_apply_rekeys_writes_backup_and_verifies(tmp_path):
    db = tmp_path / "smoke.db"
    _seed_db(db)
    r = _run(MIGRATE, str(db), PYTHON)
    assert r.returncode == 0, r.stderr
    # collect_mapping assigns numeric ids (1..N); the re-key is position 2.
    assert "111/1/Pimlico/walk" in _node_ids(db)
    assert (tmp_path / "smoke.db.pre-person-id-migration").exists()
    assert "migration applied + verified" in (r.stdout + r.stderr)


def test_second_run_is_a_noop(tmp_path):
    db = tmp_path / "smoke.db"
    _seed_db(db)
    assert _run(MIGRATE, str(db), PYTHON).returncode == 0
    after_first = _node_ids(db)
    backup_size = (tmp_path / "smoke.db.pre-person-id-migration").stat().st_size
    r = _run(MIGRATE, str(db), PYTHON)
    assert r.returncode == 0
    assert _node_ids(db) == after_first
    # the second apply must not re-write or re-backup (idempotence)
    assert (tmp_path / "smoke.db.pre-person-id-migration").stat().st_size == backup_size


def test_space_gate_refuses_below_threshold(tmp_path):
    db = tmp_path / "smoke.db"
    _seed_db(db)
    r = _run(MIGRATE, str(db), PYTHON, env={"HOUSES_MIGRATION_MIN_FREE_BYTES": "9999999999999999"})
    assert r.returncode != 0
    assert "refusing" in (r.stdout + r.stderr).lower()
    assert "111/Simon/Pimlico/walk" in _node_ids(db)  # nothing was touched


def test_missing_script_fails_loudly(tmp_path):
    db = tmp_path / "smoke.db"
    _seed_db(db)
    r = _run(str(tmp_path / "nope.py"), str(db), PYTHON)
    assert r.returncode != 0
    assert "missing" in (r.stdout + r.stderr).lower()


def test_explicit_dry_run_flag_writes_nothing(tmp_path):
    db = tmp_path / "smoke.db"
    _seed_db(db)
    before = _node_ids(db)
    r = _run("--dry-run", MIGRATE, str(db), PYTHON)
    assert r.returncode == 0, r.stderr
    assert _node_ids(db) == before
    assert "dry-run only" in (r.stdout + r.stderr)