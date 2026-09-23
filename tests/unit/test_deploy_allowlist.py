"""Regression tests for the deploy-key allowlist dispatcher.

2026-09-23: the old `command="… release.sh $1 …"` entry swallowed every
arg-bearing invocation — `switch.sh --rollback` and `--diagnose` no-opped
with exit 0. These tests pin the $SSH_ORIGINAL_COMMAND-based semantics:
sanctioned shapes forward EXACTLY, everything else is a silent no-op or a
validated rejection.
"""

# lucidlint: ignore-file fakefs — the dispatcher is a separate process
# (subprocess interop needs a real filesystem: a stub `sudo` executable
# shadowing PATH, exactly what testing-standards.md's subprocess carve-out
# permits)
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DISPATCHER = REPO / "tools" / "deploy" / "deploy-allowlist.sh"


def _run(stubsudo, command: str):
    env = {
        "PATH": f"{stubsudo}:/usr/bin:/bin",
        "SSH_ORIGINAL_COMMAND": command,
    }
    r = subprocess.run(["sh", str(DISPATCHER)], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def _stub_sudo(tmp_path):
    stubsudo = tmp_path / "stubsudo"
    stubsudo.mkdir(exist_ok=True)
    (stubsudo / "sudo").write_text('#!/bin/sh\necho "SUDO:$@"\nexit 0\n')
    (stubsudo / "sudo").chmod(0o755)
    return stubsudo


def test_sanctioned_shapes_forward_exactly(tmp_path):
    cases = [
        ("sudo /opt/houses/release.sh main", "SUDO:/opt/houses/release.sh main"),
        ("sudo /opt/houses/release.sh v1.5.4", "SUDO:/opt/houses/release.sh v1.5.4"),
        ("sudo /opt/houses/switch.sh", "SUDO:/opt/houses/switch.sh"),
        ("sudo /opt/houses/switch.sh --rollback", "SUDO:/opt/houses/switch.sh --rollback"),
        ("sudo /opt/houses/switch.sh --diagnose", "SUDO:/opt/houses/switch.sh --diagnose"),
        (
            "sudo journalctl -u houses-blue -n 40 --no-pager",
            "SUDO:journalctl -u houses-blue -n 40 --no-pager",
        ),
        ("sudo journalctl -n 10 --no-pager", "SUDO:journalctl -n 10 --no-pager"),
        ("sudo journalctl --no-pager", "SUDO:journalctl --no-pager"),
    ]
    for command, expected in cases:
        rc, out, _ = _run(_stub_sudo(tmp_path), command)
        assert rc == 0, (command, rc, out)
        assert out == expected, (command, out)


def test_injection_and_unknown_shapes_refused(tmp_path):
    cases = [
        # injection attempts — validated against the strict charsets
        ("sudo /opt/houses/release.sh 'main;rm -rf /'", 1, "bad ref"),
        ("sudo journalctl -u /etc/passwd -n 40 --no-pager", 1, "bad unit"),
        ("sudo journalctl --output=json --no-pager", 1, "unexpected"),
        ("sudo journalctl -u houses-blue -n 40 --no-pager -x", 1, "unexpected"),
        # non-sanctioned shapes: silent no-op, exit 0, NOTHING forwarded
        ("sudo /opt/houses/switch.sh --rm -rf /", 0, ""),
        ("rm -rf /", 0, ""),
        ("", 0, ""),
    ]
    for command, want_rc, want_text in cases:
        rc, out, err = _run(_stub_sudo(tmp_path), command)
        assert rc == want_rc, (command, rc, out, err)
        assert "SUDO:" not in out, (command, out)
        if want_rc != 0:
            assert want_text in err, (command, err)