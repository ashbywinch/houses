"""The base image is load-bearing, or it is a lie.

The image exists so a rollout never runs apt. If the bootstrap installs the OS
layer itself, the image's absence is invisible and every provision pays for it
(2026-10-27: 120.7s of a 376.9s bootstrap, on an image that was supposed to have
pre-installed exactly those packages). If the bake does not verify its own work,
a partial image ships and the failure surfaces as a rollout bug rather than a
bake bug — which is how the 120.7s went unnoticed.

So the contract is: the bake installs AND verifies; the bootstrap verifies only,
and its failure message points at the image.
"""

from __future__ import annotations

from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "tools" / "deploy"
BASE_IMAGE = DEPLOY / "base-image.sh"
BOOTSTRAP = DEPLOY / "box-bootstrap.sh"

# The OS layer both sides must agree on.
REQUIRED_TOOLS = ("sqlite3", "curl", "gpg", "gsutil", "caddy")
REQUIRED_DIRS = ("/opt/houses/data", "/etc/caddy/certs")


def test_the_bootstrap_never_installs_the_os_layer():
    src = BOOTSTRAP.read_text()
    assert "apt-get install" not in src, (
        "box-bootstrap.sh installs packages. The base image then stops being "
        "load-bearing: every rollout pays the apt cost and a stale image is "
        "invisible. Add the package to base-image.sh and re-bake instead."
    )
    assert "apt-get update" not in src, (
        "box-bootstrap.sh updates apt lists. It verifies the image; it never "
        "reaches the mirrors."
    )


def test_the_failure_points_at_the_image_not_the_bootstrap():
    src = BOOTSTRAP.read_text()
    assert "action=bake" in src, (
        "a bootstrap that fails on a missing OS-layer item must name the "
        "remedy (re-bake the image), or the next agent patches the bootstrap"
    )
    assert "Do NOT install" in src or "Do NOT" in src, (
        "the failure must explicitly forbid patching the bootstrap — that is "
        "the bodge that produced the cost in the first place"
    )


def test_both_sides_verify_the_same_layer():
    for path in (BASE_IMAGE, BOOTSTRAP):
        src = path.read_text()
        for tool in REQUIRED_TOOLS:
            assert tool in src, f"{path.name} does not check for {tool}"
        for directory in REQUIRED_DIRS:
            assert directory in src, f"{path.name} does not check for {directory}"


def test_the_bake_verifies_before_it_powers_off():
    src = BASE_IMAGE.read_text()
    verify_at = src.find("OS layer verified")
    assert verify_at != -1, "the bake never verifies its own layer"
    poweroff_at = src.find("shutdown -h now")
    assert poweroff_at != -1, "the bake no longer powers off (the completion signal)"
    assert verify_at < poweroff_at, (
        "the bake must verify BEFORE powering off: powering off is the "
        "completion signal the image workflow trusts"
    )
    assert "BAKE FAILED" in src, (
        "a failed verify must say so loudly — the bake log is what the operator "
        "reads when provisioning breaks"
    )
