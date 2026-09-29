"""Regression tests for the deploy-key allowlist dispatcher.

2026-09-23: the old `command="… release.sh $1 …"` entry swallowed every
arg-bearing invocation — `switch.sh --rollback` and `--diagnose` no-opped with
exit 0. These tests pin the $SSH_ORIGINAL_COMMAND-based semantics: sanctioned
shapes forward EXACTLY, everything else is a silent no-op or a validated
rejection.

The sanctioned set is the rollout's whole control plane
(docs/anti-fragile-rollout-plan.md): install-artifact.sh <object>,
switch.sh --snapshot|--rebase [rows]|--diagnose, and read-only journalctl. The
box never flips traffic, so no shape here moves routes.
"""

# lucidlint: ignore-file fakefs — the dispatcher is a separate process
# (subprocess interop needs a real filesystem: a stub `sudo` executable
# shadowing PATH, exactly what testing-standards.md's subprocess carve-out
# permits)
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DISPATCHER = REPO / "tools" / "deploy" / "deploy-allowlist.sh"
SHA256_HEX_CHARS = 64  # the artifact's key is its sha256, in hex
ARTIFACT = f"gs://houses-artifacts/{'a' * SHA256_HEX_CHARS}.tar.gz"
STUB_SUDO_MODE = 0o755


def _run(stubsudo, command: str):
    # lucidlint: ignore record-shape the env for a subprocess IS a key/value
    # mapping — that is the interface, not a domain record
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
    (stubsudo / "sudo").chmod(STUB_SUDO_MODE)
    return stubsudo


def test_sanctioned_shapes_forward_exactly(tmp_path):
    cases = [
        (
            f"sudo /opt/houses/install-artifact.sh {ARTIFACT}",
            f"SUDO:/opt/houses/install-artifact.sh {ARTIFACT}",
        ),
        ("sudo /opt/houses/switch.sh --snapshot", "SUDO:/opt/houses/switch.sh --snapshot"),
        ("sudo /opt/houses/switch.sh --rebase 862143", "SUDO:/opt/houses/switch.sh --rebase 862143"),
        ("sudo /opt/houses/switch.sh --diagnose", "SUDO:/opt/houses/switch.sh --diagnose"),
        ("sudo /opt/houses/switch.sh --unfreeze", "SUDO:/opt/houses/switch.sh --unfreeze"),
        (
            "sudo /opt/houses/switch.sh --restore gs://houses-seed/latest.db",
            "SUDO:/opt/houses/switch.sh --restore gs://houses-seed/latest.db",
        ),
    ]
    for command, expected in cases:
        rc, out, _ = _run(_stub_sudo(tmp_path), command)
        assert rc == 0, (command, rc, out)
        assert out == expected, (command, out)


def test_malformed_arguments_are_refused(tmp_path):
    hexish = "a" * SHA256_HEX_CHARS
    short = hexish[:-1]
    cases = [
        # the artifact must be a content-addressed object, nothing else
        ("sudo /opt/houses/install-artifact.sh /etc/passwd", 1, "bad artifact object"),
        ("sudo /opt/houses/install-artifact.sh gs://houses-artifacts/latest.tar.gz", 1, "sha256"),
        (f"sudo /opt/houses/install-artifact.sh gs://houses-artifacts/{short}.tar.gz", 1, "sha256"),
        (f"sudo /opt/houses/install-artifact.sh gs://houses-artifacts/{hexish.upper()}.tar.gz", 1, "sha256"),
        (f"sudo /opt/houses/install-artifact.sh gs://houses-artifacts/{hexish}", 1, "bad artifact object"),
        ("sudo /opt/houses/install-artifact.sh gs://bad-bucket!!/x.tar.gz", 1, "bad bucket"),
        # a recognised shape with junk after it is a loud rejection, not a no-op
        (f"sudo /opt/houses/install-artifact.sh {ARTIFACT}; rm -rf /", 1, "bad artifact object"),
        # the exception path takes an OBJECT, nothing else
        ("sudo /opt/houses/switch.sh --restore /etc/passwd", 1, "must be gs://"),
        ("sudo /opt/houses/switch.sh --restore gs://houses-seed/latest.txt", 1, "must be gs://"),
        ("sudo /opt/houses/switch.sh --restore gs://bad-bucket!!/x.db", 1, "bad bucket"),
    ]
    for command, want_rc, want_text in cases:
        rc, out, err = _run(_stub_sudo(tmp_path), command)
        assert rc == want_rc, (command, rc, out, err)
        assert "SUDO:" not in out, (command, out)
        assert want_text in err, (command, err)


def test_unsanctioned_shapes_are_silent_no_ops(tmp_path):
    """Exit 0 and forward NOTHING: the forced-command fallback must never look
    like success with a side effect. Includes the retired per-box flip and the
    retired git-ref release."""
    cases = [
        "sudo /opt/houses/switch.sh --rollback",
        "sudo /opt/houses/release.sh main",
        "sudo /opt/houses/switch.sh --rm -rf /",
        "sudo journalctl -u houses.service -n 40 --no-pager",
        "rm -rf /",
    ]
    for command in cases:
        rc, out, err = _run(_stub_sudo(tmp_path), command)
        assert rc == 0, (command, rc, out, err)
        assert out == "", (command, out)
        assert err == "", (command, err)
