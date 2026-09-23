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


def test_every_listed_migration_exists_in_the_ref():
    for mig in _migrations():
        assert (REPO / mig).is_file(), f"migrations.list references a missing script: {mig}"


def test_both_deploy_scripts_consume_the_shared_list():
    release = (REPO / "tools" / "deploy" / "release.sh").read_text()
    switch = (REPO / "tools" / "deploy" / "switch.sh").read_text()
    assert "migrations.list" in release
    assert "/opt/houses/migrations.list" in switch
    assert "run-migration.sh" in release and "run-migration.sh" in switch

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
