"""The shared migration list contract (tools/deploy/migrations.list).

One list drives BOTH the release-time rehearsal (switch copy) and the
flip-time real run (live DB): every listed migration must exist in the
ref, and both deploy scripts must consume the SAME file — a divergence
here silently stops migrations from ever running at the flip.
"""

# lucidlint: ignore-file fakefs the code under test IS the repo's deploy
# scripts and list (read from the real checkout — the same carve-out as
# test_deploy_run_migration: real-file interop).
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
LIST = REPO / "tools" / "deploy" / "migrations.list"


def _migrations() -> list[str]:
    lines = [ln.strip() for ln in LIST.read_text().splitlines()]
    return [ln for ln in lines if ln and not ln.startswith("#")]


def test_shared_list_is_newline_terminated():
    """The last-line bash trap: `while IFS= read -r MIG` does NOT execute
    its body for a final line without a newline (bash read returns
    non-zero) — the migration path silently never runs while every
    comment 'happens' to be consumed. 2026-09-25: the ref's list shipped
    with no trailing newline; the flip reported success with the migration
    never executed (no mark, no transcript, zero seconds)."""
    assert LIST.read_text().endswith("\n"), (
        "migrations.list must end with a newline — a trailing partial line "
        "is silently skipped by the while-read loops"
    )


def test_last_line_without_newline_is_still_migrated(tmp_path):
    """The deploy loops must not depend on the file's trailing newline —
    the hardening (|| [ -n "$MIG" ]) processes a partial final line."""
    import subprocess

    migration = "scripts/backfill_person_ids.py"
    runner = tmp_path / "run-migration.sh"
    runner.write_text("#!/bin/bash\necho \"$1\" >> \"$ARGS_LOG\"\n")
    runner.chmod(0o755)
    green = tmp_path / "green"
    (green / "scripts").mkdir(parents=True)
    (green / migration).write_text("")
    green_smoke = tmp_path / "green-smoke.db"
    green_smoke.write_text("")
    # THE trap: the final migration line has NO trailing newline
    (tmp_path / "migrations.list").write_text(
        "# Ref-shipped data migrations, one path per line\n" + migration
    )

    release_loop = """while IFS= read -r MIG || [ -n "$MIG" ]; do
  [ -z "$MIG" ] && continue
  [[ "$MIG" == \\#* ]] && continue
  "$RUNNER" "$ROOT/$SIDE/$MIG" "$ROOT/$SIDE-smoke.db" "$PY"
done < "$LIST"
"""
    env = {
        "ROOT": str(tmp_path),
        "SIDE": "green",
        "RUNNER": str(runner),
        "LIST": str(tmp_path / "migrations.list"),
        "PY": "/bin/true",
        "ARGS_LOG": str(tmp_path / "args.log"),
        "PATH": "/usr/bin:/bin",
    }
    r = subprocess.run(["bash", "-c", release_loop], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    invoked = (tmp_path / "args.log").read_text().splitlines()
    assert invoked == [str(green / migration)], (
        f"the newline-less last line was skipped by the loop: {invoked}"
    )


def test_every_listed_migration_exists_in_the_ref():
    for mig in _migrations():
        assert (REPO / mig).is_file(), f"migrations.list references a missing script: {mig}"


def test_both_deploy_scripts_consume_the_shared_list():
    release = (REPO / "tools" / "deploy" / "release.sh").read_text()
    switch = (REPO / "tools" / "deploy" / "switch.sh").read_text()
    assert "migrations.list" in release
    assert "/opt/houses/migrations.list" in switch
    assert "run-migration.sh" in release and "run-migration.sh" in switch
    assert "while IFS= read -r MIG" in release and "while IFS= read -r MIG" in switch

def test_both_loops_skip_comment_lines(tmp_path):
    """The migration loops consume the file directly — a comment header
    must never become a migration path. This is the regression behind the
    v1.5.2 flips failing with 'ref is missing its migration script: /# '."""
    import subprocess

    migration = "scripts/backfill_person_ids.py"
    runner = tmp_path / "run-migration.sh"
    runner.write_text("#!/bin/bash\necho \"$1\" >> \"$ARGS_LOG\"\n")
    runner.chmod(0o755)
    green = tmp_path / "green"
    (green / "scripts").mkdir(parents=True)
    (green / migration).write_text("")
    green_smoke = tmp_path / "green-smoke.db"
    green_smoke.write_text("")
    (tmp_path / "migrations.list").write_text(
        "# Ref-shipped data migrations, one path per line\n" + migration + "\n"
    )

    release_loop = """while IFS= read -r MIG; do
  [ -z "$MIG" ] && continue
  [[ "$MIG" == \\#* ]] && continue
  "$RUNNER" "$ROOT/$SIDE/$MIG" "$ROOT/$SIDE-smoke.db" "$PY"
done < "$LIST"
"""
    env = {
        "ROOT": str(tmp_path),
        "SIDE": "green",
        "RUNNER": str(runner),
        "LIST": str(tmp_path / "migrations.list"),
        "PY": "/bin/true",
        "ARGS_LOG": str(tmp_path / "args.log"),
        "PATH": "/usr/bin:/bin",
    }
    r = subprocess.run(["bash", "-c", release_loop], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    invoked = (tmp_path / "args.log").read_text().splitlines()
    assert invoked == [str(green / migration)], invoked

    # the SWITCH loop is a sibling with a different path scheme (ROOT/$NEW/MIG,
    # live DB) — its skip logic must not regress separately.
    args2 = tmp_path / "args2.log"
    runner2 = tmp_path / "run-migration2.sh"
    runner2.write_text("#!/bin/bash\necho \"$1\" >> \"$ARGS_LOG2\"\n")
    runner2.chmod(0o755)
    (tmp_path / "blue").mkdir(exist_ok=True)
    switch_loop = """if [ ! -f "$MIG_LIST" ]; then exit 1; fi
while IFS= read -r MIG; do
  [ -z "$MIG" ] && continue
  [[ "$MIG" == \#* ]] && continue
  "$RUNNER2" "$ROOT/$NEW/$MIG" "$ROOT/data/houses.db" "$PY"
done < "$MIG_LIST"
"""
    env2 = {
        "ROOT": str(tmp_path),
        "NEW": "green",
        "RUNNER2": str(runner2),
        "MIG_LIST": str(tmp_path / "migrations.list"),
        "PY": "/bin/true",
        "ARGS_LOG2": str(args2),
        "PATH": "/usr/bin:/bin",
    }
    r2 = subprocess.run(["bash", "-c", switch_loop], capture_output=True, text=True, env=env2)
    assert r2.returncode == 0, r2.stderr
    invoked2 = args2.read_text().splitlines()
    assert invoked2 == [str(green / migration)], invoked2


def test_switch_guard_refuses_missing_migrations_list(tmp_path):
    """A flip with no shipped /opt/houses/migrations.list must stop cold —
    the v1.5.x silent-skip failure mode."""
    import subprocess

    switch_src = (REPO / "tools" / "deploy" / "switch.sh").read_text()
    assert "migrations.list missing on the box" in switch_src
    assert "refusing to flip" in switch_src
    missing_dir = tmp_path / "opt" / "houses"
    missing_dir.mkdir(parents=True)
    env = {"PATH": "/usr/bin:/bin", "MIG_LIST": str(missing_dir / "migrations.list")}
    r = subprocess.run(
        ["bash", "-c", "if [ ! -f \"$MIG_LIST\" ]; then echo 'refusing to flip'; exit 1; fi"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert r.returncode != 0
    assert "refusing to flip" in r.stdout


def test_release_requires_a_verified_runner_log(tmp_path):
    """The rehearsal must leave a runner log with the verified marker; a
    silently-skipped migration (no fresh log) fails the release."""
    import subprocess
    import time

    logs = tmp_path / "logs" / "releases"
    logs.mkdir(parents=True)
    mark = tmp_path / ".release-migration-start"
    mark.write_text("")

    # a fresh-enough log WITHOUT the verified marker -> refuse
    stale = logs / "run-migration-early-whatever.log"
    stale.write_text("dry-run on /x\n")
    r = subprocess.run(
        [
            "bash",
            "-c",
            'NEWEST=$(find "$LOGS" -name \'run-migration-*.log\' -newer "$MARK" | head -1 || true); '
            'if [ -z "$NEWEST" ] || ! grep -q "migration applied + verified" "$NEWEST"; then '
            'echo "no verified runner log — refusing"; exit 1; fi; echo "ok: $NEWEST"',
        ],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "LOGS": str(logs), "MARK": str(mark)},
    )
    assert r.returncode != 0, r.stdout + r.stderr
    assert "refusing" in r.stdout

    # with the marker present -> passes (mark BEFORE the log: -newer finds it)
    mark_new = tmp_path / ".mark2"
    mark_new.write_text("")
    verified = logs / "run-migration-now-x.log"
    verified.write_text("migration applied + verified\n")
    time.sleep(0.05)  # log mtime strictly after the mark (coarse-FS safety)
    r2 = subprocess.run(
        [
            "bash",
            "-c",
            'NEWEST=$(find "$LOGS" -name \'run-migration-*.log\' -newer "$MARK" | head -1 || true); '
            'if [ -z "$NEWEST" ] || ! grep -q "migration applied + verified" "$NEWEST"; then '
            'echo "no verified runner log — refusing"; exit 1; fi; echo "ok"',
        ],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "LOGS": str(logs), "MARK": str(mark_new)},
    )
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert "ok" in r2.stdout
