"""`install-artifact.sh` end to end on its NORMAL path.

The rollout's install step takes the fast path whenever the box already runs the
requested artifact — which is the ordinary case, because `provision` bootstraps
the standby from the same artifact the install job then asks for. That path ran
the venv receipt, the migration rehearsal and the whole smoke and then died on
its last line (`ACTUAL_SHA: unbound variable` under `set -u`), so no rollout could
reach the human gate while every earlier stage looked healthy.

This test drives the REAL script with stubbed externals, so that class stays
caught. It asserts the observable outcome: exit 0, the INSTALL READY verdict
naming the artifact, and the app left STOPPED.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "tools" / "deploy" / "install-artifact.sh"
SHA256_HEX_CHARS = 64  # the artifact's key IS its sha256, in hex
SHA = "a" * SHA256_HEX_CHARS
ARTIFACT = f"gs://houses-artifacts/{SHA}.tar.gz"
EXECUTABLE = 0o755
STUB_PYTHON = """#!/bin/sh
# The artifact's venv interpreter, as the installer uses it: the import receipt,
# the JSON helpers, the cookie mint, and the migration runner (whose verdict line
# is the gate the installer greps for).
case "$*" in
  *"--manifest"*) printf 'migrations: 1 applied+checked, 0 failed\\n' ;;
  *"import houses, dag"*) exit 0 ;;
  *"import sys"*) printf '3.12.0\\n' ;;
  *"pending"*) printf '0\\n' ;;          # the scrape-queue probe
  *"json,sys"*) printf '1\\n' ;;         # the property count and a rid
  *"_make_session_cookie"*) printf 'stub-cookie\\n' ;;
esac
exit 0
"""
STUB_CURL = """#!/bin/sh
# Honours -o FILE and -w CODE like the real thing, because the installer checks
# both the body it wrote and the status code it read.
out=""; want_code=""; url=""; prev=""
for arg in "$@"; do
  case "$prev" in -o) out="$arg" ;; -w) want_code="$arg" ;; esac
  case "$arg" in http*|localhost*) url="$arg" ;; esac
  prev="$arg"
done
case "$url" in
  *"/health"*) body='{"status":"ok","db":"ok","last_write":"2026-09-25T00:00:00+00:00"}' ;;
  */api/properties/all*) body='{"properties":{"111":{}}}' ;;
  *) body='<!doctype html><html><body>ok</body></html>' ;;
esac
[ -z "$out" ] || printf '%s' "$body" > "$out"
if [ -n "$want_code" ]; then printf '200'; else printf '%s' "$body"; fi
exit 0
"""
STUB_SYSTEMCTL = """#!/bin/sh
printf 'systemctl %s\\n' "$*" >> "${SYSTEMCTL_LOG}"
exit 0
"""
STUB_SWITCH = """#!/bin/sh
# The one writer of the role URL: --public-url appends to the env file the
# installer delegates to, exactly like the real switch.sh.
case "$1" in
  --public-url) printf 'HOUSES_PUBLIC_URL=%s\n' "$2" >> "${HOUSES_ENV_FILE:-/etc/houses.env}" ;;
esac
exit 0
"""
STUB_ID = """#!/bin/sh
# The installer must think it runs as root (the box's deploy key does).
case "$1" in -u) printf '0\\n' ;; *) exit 0 ;; esac
exit 0
"""
STUB_GSUTIL = """#!/bin/sh
# `gsutil -q cp <src> <dst>`: the sandbox's fetch stub substitutes the LOCAL
# tarball named by GSUTIL_SRC for the fake GCS object — the installer still
# verifies the object by hashing the bytes it fetched (the name IS the proof),
# so the stub only supplies the bytes.
if [ "$1" = "-q" ]; then shift; fi
cp "${GSUTIL_SRC:?}" "$3"
"""
STUB_SCHEMA = (
    "CREATE TABLE node_results (id INTEGER PRIMARY KEY, node_id TEXT, result_json BLOB,"
    " dep_timestamps TEXT, created_at TEXT, code_version TEXT);"
)


def _box(tmp_path: Path) -> Path:
    """A box whose checkout is already the requested artifact (the fast path)."""
    root = tmp_path / "opt" / "houses"
    (root / "app" / ".venv" / "bin").mkdir(parents=True)
    (root / "app" / "tools" / "deploy").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "logs" / "releases").mkdir(parents=True)
    (root / "app" / "serve_prod.py").write_text("")
    (root / "app" / "tools" / "deploy" / "box-setup.sh").write_text("exit 0\n")
    # The one writer of the role URL: switch.sh --public-url (the installer
    # delegates to it — maintain the single-writer rule in the sandbox too).
    (root / "switch.sh").write_text(
        STUB_SWITCH
    )
    (root / "switch.sh").chmod(EXECUTABLE)
    (root / "app" / "tools" / "deploy" / "run_migrations.py").write_text("")
    (root / "app" / "tools" / "deploy" / "migrations.list").write_text(
        "scripts/backfill_person_ids.py scripts/backfill_person_ids.check.py\n"
    )
    interpreter = root / "app" / ".venv" / "bin" / "python"
    interpreter.write_text(STUB_PYTHON)
    interpreter.chmod(EXECUTABLE)
    database = root / "data" / "houses.db"
    conn = sqlite3.connect(database)
    conn.executescript(STUB_SCHEMA)
    conn.execute(
        "INSERT INTO node_results (node_id, result_json, dep_timestamps, created_at)"
        " VALUES ('persons','','{}','2026-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()
    # The marker the bootstrap writes: which sha this checkout came from.
    (root / "ARTIFACT").write_text(f"sha256={SHA} ref=main unpacked_at=2026-09-25T00:00:00Z\n")
    return root


def _stubs(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "stubs"
    bin_dir.mkdir()
    for name, body in (
        ("curl", STUB_CURL),
        ("systemctl", STUB_SYSTEMCTL),
        ("id", STUB_ID),
        ("gsutil", STUB_GSUTIL),
    ):
        executable = bin_dir / name
        executable.write_text(body)
        executable.chmod(EXECUTABLE)
    return bin_dir


def test_the_fast_path_reaches_review_ready_and_leaves_the_app_serving(tmp_path):
    root = _box(tmp_path)
    bin_dir = _stubs(tmp_path)
    env_file = tmp_path / "houses.env"
    env_file.write_text("HOUSES_SESSION_SECRET=secret\n")
    systemctl_log = tmp_path / "systemctl.log"
    systemctl_log.write_text("")

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOUSES_ROOT": str(root),
        "HOUSES_ENV_FILE": str(env_file),
        "SYSTEMCTL_LOG": str(systemctl_log),
    }
    result = subprocess.run([str(INSTALLER), ARTIFACT], capture_output=True, text=True, env=env)
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "the box already runs" in output, "this test must exercise the fast path"
    assert f"REVIEW READY: artifact {SHA}" in output
    # The rehearsal and the smoke really ran before that verdict.
    assert "migrations: 1 applied+checked, 0 failed" in output
    assert "house records served: 1" in output
    # The standby IS the review surface: the app stays SERVING (never stopped),
    # and the standby's role URL (houses-smoke) was written into the env the
    # app reads at its next start.
    calls = systemctl_log.read_text().splitlines()
    assert "systemctl stop houses.service" not in calls, calls
    assert "HOUSES_PUBLIC_URL=https://houses-smoke.blueumbrella.net" in env_file.read_text()


def _tarball(tmp_path: Path) -> tuple[Path, str]:
    """A REAL artifact tarball (tools/deploy + units + ARTIFACT_REF) whose
    sha256 IS its object key — the installer verifies what it fetched."""
    stage = tmp_path / "tarball-src"
    (stage / "tools" / "deploy").mkdir(parents=True)
    (stage / "units").mkdir()
    (stage / "tools" / "deploy" / "box-setup.sh").write_text("#!/bin/sh\nexit 0\n")
    (stage / "tools" / "deploy" / "switch.sh").write_text("#!/bin/sh\nexit 0\n")
    (stage / "tools" / "deploy" / "run_migrations.py").write_text("")
    (stage / "tools" / "deploy" / "migrations.list").write_text("m a\n")
    (stage / "units" / "houses.service").write_text("")
    (stage / "ARTIFACT_REF").write_text("main\n")
    tarball = tmp_path / "artifact.tar.gz"
    with tarfile.open(tarball, "w:gz") as tf:
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                tf.add(p, arcname=p.relative_to(stage))
    return tarball, hashlib.sha256(tarball.read_bytes()).hexdigest()


def test_tooling_only_sync_never_stops_or_swaps_the_app(tmp_path):
    """2026-10-02: install-artifact.sh --tooling-only stopped the standby's app
    (and swapped its whole checkout) before the flip's preflight — killing the
    review surface the preflight checks. A tooling sync must refresh ONLY the
    tooling: no systemctl, no app swap, no ARTIFACT rewrite."""
    root = _box(tmp_path)  # ARTIFACT marker names SHA (the OLD artifact)
    bin_dir = _stubs(tmp_path)
    env_file = tmp_path / "houses.env"
    env_file.write_text("HOUSES_SESSION_SECRET=secret\n")
    systemctl_log = tmp_path / "systemctl.log"
    systemctl_log.write_text("")
    tarball, sha = _tarball(tmp_path)
    obj = f"gs://houses-artifacts/{sha}.tar.gz"

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOUSES_ROOT": str(root),
        "GSUTIL_SRC": str(tarball),
        "HOUSES_ENV_FILE": str(env_file),
        "SYSTEMCTL_LOG": str(systemctl_log),
    }
    result = subprocess.run(
        [str(INSTALLER), "--tooling-only", obj], capture_output=True, text=True, env=env
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "TOOLING now at" in output
    # The app was NEVER touched: no systemctl call at all.
    assert systemctl_log.read_text() == "", "a tooling-only sync must not stop or restart the app"
    # The APP's marker still names the old artifact; the TOOLING marker names the new.
    assert f"sha256={SHA} ref=main" in (root / "ARTIFACT").read_text()
    tooling = (root / "TOOLING").read_text()
    assert f"sha256={sha} ref=main" in tooling
    # The app tree was not swapped: no staging dir left behind, old app intact.
    assert not (root / ".app-incoming").exists()
    assert (root / "app" / ".venv" / "bin" / "python").exists()
    # A second sync of the same object is a no-op (the TOOLING marker matches).
    again = subprocess.run(
        [str(INSTALLER), "--tooling-only", obj], capture_output=True, text=True, env=env
    )
    assert again.returncode == 0, again.stdout + again.stderr
    assert "tooling already at" in again.stdout + again.stderr
    assert systemctl_log.read_text() == ""
