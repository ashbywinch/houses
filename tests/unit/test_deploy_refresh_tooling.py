"""`refresh-tooling.sh` — the narrow tooling refresh on a serving box.

The contract this pins (2026-10-29: an automatic owner refresh ran the machine
bootstrap on the live box, stopped production and left it stopped):

* unchanged files ⇒ NOTHING is written (not even the provenance marker) and no
  systemd command runs;
* a changed script is replaced atomically, the previous copy is kept, and the
  TOOLING marker records the artifact it came from;
* a script that fails validation aborts BEFORE anything is replaced;
* a unit-file change runs `systemctl daemon-reload` and nothing else — never
  start/stop/enable/restart, which is what could take the box out of service.
"""

# lucidlint: ignore-file fakefs — the refresher is a separate process; its
# sandbox needs real files and stub executables shadowing PATH (the subprocess
# interop carve-out tests/unit/test_deploy_allowlist.py documents).

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class Fixture:
    """What the refresher owns, and the values every run in this file shares."""

    REPO = Path(__file__).resolve().parents[2]
    REFRESHER = REPO / "tools" / "deploy" / "refresh-tooling.sh"
    SHA256_HEX_CHARS = 64  # the artifact's key IS its sha256, in hex
    SHA = "b" * SHA256_HEX_CHARS
    REF = "v-test"
    EXECUTABLE = 0o755
    # Every file the refresher owns; the artifact tree must carry all of them.
    SCRIPTS = (
        "run-instance.sh",
        "switch.sh",
        "install-artifact.sh",
        "deploy-allowlist.sh",
        "install-deploy-allowlist.sh",
        "network-watchdog.sh",
        "refresh-tooling.sh",
    )
    UNITS = ("houses.service", "houses-network-watchdog.service", "houses-network-watchdog.timer")


@dataclass(frozen=True)
class Sandbox:
    """One run: the artifact tree, the box root, the unit dir and the result."""

    artifact: Path
    root: Path
    units: Path
    result: subprocess.CompletedProcess[str]


def _write(path: Path, body: str, mode: int = 0o644, *, only_if_missing: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if only_if_missing and path.exists():
        return
    path.write_text(body)
    path.chmod(mode)


def _prepare(tmp_path: Path, *, broken_switch: bool = False) -> None:
    """Lay out an artifact tree, a box root holding the OLD copies, and a unit
    dir holding OLD units. Idempotent: a second call leaves the box as it is,
    which is what makes the no-op test meaningful."""
    artifact = tmp_path / "artifact"
    root = tmp_path / "root"
    units = tmp_path / "units"
    stub = tmp_path / "stub"
    for path in (root, units, stub):
        path.mkdir(exist_ok=True)

    _write(stub / "id", '#!/bin/sh\ncase "$1" in -u) printf 0 ;; esac\nexit 0\n', Fixture.EXECUTABLE)
    _write(
        stub / "systemctl",
        f'#!/bin/sh\necho "$@" >> "{tmp_path / "systemctl.log"}"\nexit 0\n',
        Fixture.EXECUTABLE,
    )

    for name in Fixture.SCRIPTS:
        new_body = "#!/bin/sh\necho new\n"
        if name == "switch.sh" and broken_switch:
            new_body = "#!/bin/sh\nif then fi (\n"  # sh -n rejects this
        _write(artifact / "tools/deploy" / name, new_body, Fixture.EXECUTABLE)
        _write(root / name, "#!/bin/sh\necho old\n", Fixture.EXECUTABLE, only_if_missing=True)
    for name, body in (("run_migrations.py", "print('ok')\n"), ("migrations.list", "# manifest\n")):
        _write(artifact / "tools/deploy" / name, body)
        _write(root / name, body, only_if_missing=True)
    for name in Fixture.UNITS:
        _write(artifact / "tools/deploy/units" / name, "[Unit]\nDescription=new\n")
        _write(units / name, "[Unit]\nDescription=old\n", only_if_missing=True)


def _refresh(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Run the refresher against the sandbox laid out by `_prepare`."""
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path / 'stub'}:/usr/bin:/bin"
    env["HOUSES_ROOT"] = str(tmp_path / "root")
    env["HOUSES_UNIT_DIR"] = str(tmp_path / "units")
    return subprocess.run(
        ["sh", str(Fixture.REFRESHER), str(tmp_path / "artifact"), Fixture.SHA, Fixture.REF],
        capture_output=True,
        text=True,
        env=env,
    )


def test_unchanged_tooling_writes_nothing(tmp_path: Path) -> None:
    _prepare(tmp_path)
    first = _refresh(tmp_path)
    assert first.returncode == 0, first.stderr
    root = tmp_path / "root"
    marker = root / "TOOLING"
    before = {p.name: p.stat().st_mtime_ns for p in root.glob("*")}
    marker_before = marker.stat().st_mtime_ns

    second = _refresh(tmp_path)

    assert second.returncode == 0, second.stderr
    assert "already current" in second.stdout
    assert marker.stat().st_mtime_ns == marker_before
    assert {p.name: p.stat().st_mtime_ns for p in root.glob("*")} == before


def test_changed_script_is_replaced_and_backed_up(tmp_path: Path) -> None:
    _prepare(tmp_path)
    run = _refresh(tmp_path)
    assert run.returncode == 0, run.stderr

    root = tmp_path / "root"
    assert (root / "switch.sh").read_text() == "#!/bin/sh\necho new\n"
    assert (root / ".tooling-previous/switch.sh").read_text() == "#!/bin/sh\necho old\n"
    marker = (root / "TOOLING").read_text()
    assert Fixture.SHA in marker and Fixture.REF in marker


def test_invalid_script_aborts_before_any_write(tmp_path: Path) -> None:
    _prepare(tmp_path, broken_switch=True)
    run = _refresh(tmp_path)

    assert run.returncode == 1
    assert "failed sh -n" in run.stderr
    root = tmp_path / "root"
    assert (root / "switch.sh").read_text() == "#!/bin/sh\necho old\n", "validation precedes every write"
    assert not (root / "TOOLING").exists()


def test_unit_change_reloads_systemd_and_nothing_else(tmp_path: Path) -> None:
    _prepare(tmp_path)
    run = _refresh(tmp_path)
    assert run.returncode == 0, run.stderr

    calls = (tmp_path / "systemctl.log").read_text().splitlines()
    assert calls == ["daemon-reload"], f"only a daemon-reload may run, got {calls}"
