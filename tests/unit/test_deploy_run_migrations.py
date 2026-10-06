"""The migration runner (tools/deploy/run_migrations.py) contract.

Pins the release/flip migration step with the REAL migration
(scripts/backfill_person_ids.py) and the REAL paired check
(scripts/backfill_person_ids.check.py) against a seeded temp DB:

  * a plain apply re-keys, writes the pre-migration backup, runs the check,
    and reports exactly `migration <base>: apply ok; backup ok; check ok`
    plus `migrations: <N> applied+checked, 0 failed`;
  * a second run is idempotent;
  * a check that fails, and an apply script that skips its work, each fail
    the run with `check FAILED: <err>` — the 2026-09-24 silent skip cannot
    be reported as success;
  * a failing migration stops the chain (later entries: `not attempted`);
  * the space gate refuses before any write, and --dry-run writes nothing;
  * the parser rejects empty/comments-only/malformed/duplicate manifests.

The runner is the ONE deployment path for data migrations: a future
migration that works with these tests needs no new machinery.
"""

# lucidlint: ignore-file fakefs the code under test is the repo's own
# runner + migration + check, executed via subprocess against a real sqlite
# file — testing-standards.md's subprocess/C-level-IO carve-out, the same as
# the allowlist dispatcher test.
from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "tools" / "deploy"
RUNNER = DEPLOY / "run_migrations.py"
PAIR = "scripts/backfill_person_ids.py scripts/backfill_person_ids.check.py"
STALE_LOGS = 40  # more than the runner keeps, so the prune has to act
KEPT_LOGS = 32  # the runner's retention: the newest 32 transcripts
STUB_CHECK = """import argparse, sys
parser = argparse.ArgumentParser()
parser.add_argument("--db")
parser.parse_args()
print("stub check: nothing verified", file=sys.stderr)
raise SystemExit(1)
"""
STUB_SKIPS_ITS_WORK = """import argparse, shutil
parser = argparse.ArgumentParser()
parser.add_argument("--db")
parser.add_argument("--backup-path")
parser.add_argument("--apply", action="store_true")
parser.add_argument("--backup", action="store_true")
parser.add_argument("--verify", action="store_true")
args = parser.parse_args()
if args.apply and args.backup:
    shutil.copy(args.db, args.backup_path)
print("stub apply: nothing migrated")
"""
STUB_NO_WRITE = """import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--db")
parser.add_argument("--backup-path")
parser.add_argument("--apply", action="store_true")
parser.add_argument("--backup", action="store_true")
parser.add_argument("--verify", action="store_true")
parser.parse_args()
print("no-write: already split (stub)")
"""
STUB_SILENT_NO_WRITE = """import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--db")
parser.add_argument("--backup-path")
parser.add_argument("--apply", action="store_true")
parser.add_argument("--backup", action="store_true")
parser.add_argument("--verify", action="store_true")
parser.parse_args()
print("stub apply: fine, nothing to report")
"""
STUB_CHECK_OK = """import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--db")
parser.parse_args()
print("stub check: verified")
"""
STUB_APPLY_FAILS = """import argparse, sys
parser = argparse.ArgumentParser()
parser.add_argument("--db")
parser.add_argument("--backup-path")
parser.add_argument("--apply", action="store_true")
parser.add_argument("--backup", action="store_true")
parser.add_argument("--verify", action="store_true")
args = parser.parse_args()
if args.apply:
    print("stub apply: refusing to write", file=sys.stderr)
    raise SystemExit(2)
print("stub apply: dry-run ok")
"""

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


def _load_runner():
    """The runner under test, as a module — parse_manifest is a pure function."""
    spec = importlib.util.spec_from_file_location("deploy_run_migrations", RUNNER)
    assert spec and spec.loader, "run_migrations.py must be importable"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolves annotations via sys.modules
    spec.loader.exec_module(module)
    return module


# ONE module object: a second load would define a second ManifestError, and
# pytest.raises would no longer recognise the exception it is meant to catch.
RUNNER_MODULE = _load_runner()


@dataclasses.dataclass(frozen=True)
class Checkout:
    """A temp checkout whose ``scripts/`` is the real one, plus its manifest.

    Stub scripts are written into ``root`` by the case that needs them.
    """

    root: Path
    manifest: Path

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    def run(
        self, db: Path, *, mode: str = "--apply", min_free_bytes: int | None = None
    ) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "HOUSES_ROOT": str(self.root),
            "HOUSES_LOG_DIR": str(self.logs),
        }
        if min_free_bytes is not None:
            env["HOUSES_MIGRATION_MIN_FREE_BYTES"] = str(min_free_bytes)
        return subprocess.run(
            [
                sys.executable, str(RUNNER),
                "--manifest", str(self.manifest),
                "--db", str(db),
                "--scripts-dir", str(self.root),
                "--python", sys.executable,
                mode,
            ],
            capture_output=True,
            text=True,
            env=env,
        )


def _checkout(tmp_path: Path, lines: list[str]) -> Checkout:
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "scripts").symlink_to(REPO / "scripts")
    manifest = tmp_path / "migrations.list"
    manifest.write_text("".join(f"{line}\n" for line in lines))
    return Checkout(root=root, manifest=manifest)


def _row(conn, node_id: str, payload) -> None:
    conn.execute(
        "INSERT INTO node_results (node_id, result_json, dep_timestamps, created_at, code_version)"
        " VALUES (?,?,?,?,?)",
        (node_id, zlib.compress(json.dumps(payload).encode()), "{}", "2026-01-01T00:00:00", "v9"),
    )


def _seed_db(path: Path) -> None:
    """One person (no person_id yet), one name-keyed node id, one
    name-keyed money payload, one row the migration must leave alone."""
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    _row(conn, "persons", {"status": "succeeded", "value": [{"name": "Simon", "has_car": True}]})
    _row(conn, "111/Simon/Pimlico/walk", {"status": "succeeded", "value": "x"})
    _row(conn, "111/works_estimates", {"status": "succeeded", "value": {"Simon": {"amount": "25000.00"}}})
    _row(conn, "9999/best_address", {"status": "succeeded", "value": "1 Test St"})
    conn.commit()
    conn.close()


def _node_ids(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    rows = [row[0] for row in conn.execute("SELECT node_id FROM node_results ORDER BY id")]
    conn.close()
    return rows


def _money_keys(path: Path, node_id: str) -> list[str]:
    conn = sqlite3.connect(path)
    blob = conn.execute("SELECT result_json FROM node_results WHERE node_id=?", (node_id,)).fetchone()[0]
    conn.close()
    return sorted(json.loads(zlib.decompress(blob).decode())["value"])


def _summary(output: str) -> str | None:
    """The runner's verdict summary line, or None when it never got there."""
    matches = re.findall(r"^migrations: .*$", output, re.MULTILINE)
    assert len(matches) <= 1, f"exactly one summary line belongs in a transcript: {matches}"
    return matches[0] if matches else None


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("", "zero entries"),
        ("# only a comment\n", "zero entries"),
        ("scripts/backfill_person_ids.py\n", "not a pair"),
        ("scripts/a.py scripts/b.py scripts/c.py\n", "not a pair"),
        ("/etc/passwd scripts/b.py\n", "absolute"),
        ("../escape.py scripts/b.py\n", "escapes the checkout"),
        ("scripts/a.py scripts/b.py\nscripts/a.py scripts/c.py\n", "listed twice"),
        ("d1/a.py scripts/b.py\nd2/a.py scripts/c.py\n", "basename"),
    ],
)
def test_the_parser_rejects_an_unusable_manifest(text, reason):
    """`reason` names the case; every one of them must raise."""
    del reason
    with pytest.raises(RUNNER_MODULE.ManifestError):
        RUNNER_MODULE.parse_manifest(text, REPO)


def test_the_parser_reads_a_final_line_with_no_trailing_newline():
    """The 2026-09-24 skip: the file's shape must not decide whether the
    migration runs."""
    entries = RUNNER_MODULE.parse_manifest(PAIR, REPO)
    assert [entry.name for entry in entries] == ["backfill_person_ids.py"]


def test_a_comments_only_manifest_is_refused_without_a_verdict(tmp_path):
    db = tmp_path / "smoke.db"
    _seed_db(db)
    result = _checkout(tmp_path, ["# header only"]).run(db)
    assert result.returncode != 0
    assert _summary(result.stdout) is None, "a run that did nothing must report no verdict"
    assert "no migrations" in result.stdout
    assert "111/Simon/Pimlico/walk" in _node_ids(db)


def test_apply_rekeys_writes_the_backup_and_runs_the_check(tmp_path):
    db = tmp_path / "smoke.db"
    _seed_db(db)
    result = _checkout(tmp_path, [PAIR]).run(db)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "mode=apply" in result.stdout
    assert "migration backfill_person_ids.py: apply ok; backup ok; check ok" in result.stdout
    assert _summary(result.stdout) == "migrations: 1 applied+checked, 0 failed"
    # the check RAN (its own line, not the runner's claim)
    assert "check ok:" in result.stdout
    # the money the migration exists to preserve, re-keyed
    assert "111/1/Pimlico/walk" in _node_ids(db)
    assert _money_keys(db, "111/works_estimates") == ["1"]
    assert (tmp_path / "smoke.db.pre-backfill_person_ids.py").is_file()



def test_a_second_apply_is_idempotent(tmp_path):
    db = tmp_path / "smoke.db"
    _seed_db(db)
    checkout = _checkout(tmp_path, [PAIR])
    assert checkout.run(db).returncode == 0
    after_first = _node_ids(db)
    second = checkout.run(db)
    assert second.returncode == 0, second.stdout + second.stderr
    assert _node_ids(db) == after_first
    assert _summary(second.stdout) == "migrations: 1 applied+checked, 0 failed"


def test_a_check_that_fails_fails_the_run(tmp_path):
    """The verdict gate: an apply that wrote without the check passing must
    not be reported as a successful migration."""
    db = tmp_path / "smoke.db"
    _seed_db(db)
    checkout = _checkout(tmp_path, ["scripts/backfill_person_ids.py stub_check.py"])
    (checkout.root / "stub_check.py").write_text(STUB_CHECK)
    result = checkout.run(db)
    assert result.returncode != 0
    assert "migration backfill_person_ids.py: check FAILED: stub check: nothing verified" in result.stdout
    assert _summary(result.stdout) == "migrations: 0 applied+checked, 1 failed"


def test_a_migration_that_skips_its_work_fails_the_check(tmp_path):
    """The 2026-09-24 shape: the ceremony ran (backup written, exit 0) and the
    data was never migrated. The paired check must catch it."""
    db = tmp_path / "smoke.db"
    _seed_db(db)
    checkout = _checkout(tmp_path, ["stub_skips.py scripts/backfill_person_ids.check.py"])
    (checkout.root / "stub_skips.py").write_text(STUB_SKIPS_ITS_WORK)
    result = checkout.run(db)
    assert result.returncode != 0
    assert "check FAILED:" in result.stdout
    assert _summary(result.stdout) == "migrations: 0 applied+checked, 1 failed"
    assert "111/Simon/Pimlico/walk" in _node_ids(db), "the skipped migration left the DB unmigrated"


def test_an_apply_that_states_it_wrote_nothing_needs_no_backup(tmp_path):
    """The idempotent no-op: a database already split writes nothing, so there
    is nothing to protect. The apply says so in its own output and the run is
    still applied+checked — without this the rollout fails on an already-split
    source (the current seed), which is what a fresh box restores."""
    db = tmp_path / "smoke.db"
    _seed_db(db)
    checkout = _checkout(tmp_path, ["stub_no_write.py stub_check_ok.py"])
    (checkout.root / "stub_no_write.py").write_text(STUB_NO_WRITE)
    (checkout.root / "stub_check_ok.py").write_text(STUB_CHECK_OK)

    result = checkout.run(db)

    assert result.returncode == 0, result.stdout
    assert "nothing to write (no-write stated); check ok" in result.stdout
    assert "backup ok" not in result.stdout, "a no-op must not claim a backup it did not take"
    assert _summary(result.stdout) == "migrations: 1 applied+checked, 0 failed"
    assert not (tmp_path / "smoke.db.pre-stub_no_write.py").exists(), "a no-op must not leave a backup"


def test_an_apply_that_writes_nothing_silently_still_fails(tmp_path):
    """The negative control for the line above: the runner believes a STATEMENT,
    not a silence. An apply that skipped the backup without saying anything is
    still a failed apply."""
    db = tmp_path / "smoke.db"
    _seed_db(db)
    checkout = _checkout(tmp_path, ["stub_silent.py stub_check_ok.py"])
    (checkout.root / "stub_silent.py").write_text(STUB_SILENT_NO_WRITE)
    (checkout.root / "stub_check_ok.py").write_text(STUB_CHECK_OK)

    result = checkout.run(db)

    assert result.returncode != 0
    assert "apply FAILED: no backup written" in result.stdout
    assert _summary(result.stdout) == "migrations: 0 applied+checked, 1 failed"


def test_a_failed_migration_stops_the_chain(tmp_path):
    db = tmp_path / "smoke.db"
    _seed_db(db)
    checkout = _checkout(tmp_path, ["stub_fails.py stub_check.py", PAIR])
    (checkout.root / "stub_fails.py").write_text(STUB_APPLY_FAILS)
    (checkout.root / "stub_check.py").write_text(STUB_CHECK)
    result = checkout.run(db)
    assert result.returncode != 0
    assert "migration stub_fails.py: apply FAILED: stub apply: refusing to write" in result.stdout
    assert (
        "migration backfill_person_ids.py: apply FAILED: not attempted — an earlier migration failed"
        in result.stdout
    )
    assert _summary(result.stdout) == "migrations: 0 applied+checked, 2 failed"
    assert not (tmp_path / "smoke.db.pre-backfill_person_ids.py").exists()


def test_the_space_gate_refuses_before_any_write(tmp_path):
    db = tmp_path / "smoke.db"
    _seed_db(db)
    result = _checkout(tmp_path, [PAIR]).run(db, min_free_bytes=9999999999999999)
    assert result.returncode != 0
    assert "refusing" in result.stdout
    assert _summary(result.stdout) is None
    assert not (tmp_path / "smoke.db.pre-backfill_person_ids.py").exists()
    assert "111/Simon/Pimlico/walk" in _node_ids(db), "nothing was touched"


def test_dry_run_reports_and_writes_nothing(tmp_path):
    db = tmp_path / "smoke.db"
    _seed_db(db)
    before = _node_ids(db)
    result = _checkout(tmp_path, [PAIR]).run(db, mode="--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "mode=dry-run" in result.stdout
    assert _summary(result.stdout) == "migrations: 1 dry-run ok, 0 failed"
    assert "applied+checked" not in result.stdout, "a dry run must not claim an apply"
    assert not (tmp_path / "smoke.db.pre-backfill_person_ids.py").exists()
    assert _node_ids(db) == before


def test_the_transcript_is_kept_and_pruned_to_the_newest_32(tmp_path):
    db = tmp_path / "smoke.db"
    _seed_db(db)
    checkout = _checkout(tmp_path, [PAIR])
    checkout.logs.mkdir()
    for index in range(STALE_LOGS):
        (checkout.logs / f"run-migrations-20200101-0000{index:02d}.log").write_text("stale\n")

    result = checkout.run(db, mode="--dry-run")
    assert result.returncode == 0, result.stderr
    kept = sorted(path.name for path in checkout.logs.glob("run-migrations-*.log"))
    assert len(kept) == KEPT_LOGS
    assert "mode=dry-run" in (checkout.logs / max(kept)).read_text(), (
        "the run's own transcript is the newest"
    )
