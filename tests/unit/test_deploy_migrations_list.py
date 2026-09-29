"""The shared migration manifest contract (tools/deploy/migrations.list) and the
shape of the rollout chain that consumes it.

One manifest drives BOTH runner calls of a rollout: the install's rehearsal (the
standby's own database) and the cutover's rebase (the restored snapshot). Its
contract:

  * every line is a `<apply-script> <check-script>` PAIR, both relative to the
    checkout root — the apply writes, the check is a separate program that opens
    the DB read-only and exits 0 iff the effect is complete;
  * both callers read it through the ONE runner (tools/deploy/run_migrations.py)
    and gate on its summary line — a shell loop here is what silently skipped the
    migration on 2026-09-24 (a final line with no trailing newline,
    comments-only to `while read`, reported as success);
  * the runner refuses the empty manifest, so "0 applied+checked, 0 failed" can
    never be the verdict of a run that did nothing.

Parser behaviour is pinned in test_deploy_run_migrations.py; this file pins the
SHIPPED manifest, the two callers' wiring, and the absence of the mechanisms the
plan deletes (the per-box flip, the git-ref release, the auto seed refresh).
"""

# lucidlint: ignore-file fakefs the code under test IS the repo's deploy scripts
# and manifest (read from the real checkout — the same carve-out as
# test_deploy_run_migrations: real-file interop).
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "tools" / "deploy"
WORKFLOW = REPO / ".github" / "workflows" / "release.yml"
MANIFEST = DEPLOY / "migrations.list"
RUNNER_CALLERS = ("install-artifact.sh", "switch.sh")
# The retired INVOCATION shapes (not bare words): the box scripts explain in
# prose which mechanisms are gone and why, and that explanation is worth keeping.
RETIRED_INVOCATIONS = (
    "switch.sh --publish",
    "switch.sh --rollback",
    "run-migration.sh",
    "/opt/houses/release.sh",
)
RETIRED_FILES = (
    "release.sh",
    "run-migration.sh",
    "provision-box.sh",
    "units/houses-blue.service",
    "units/houses-green.service",
)


def _runner_module():
    spec = importlib.util.spec_from_file_location("deploy_run_migrations", DEPLOY / "run_migrations.py")
    assert spec and spec.loader, "run_migrations.py must be importable"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_shipped_manifest_pairs_every_apply_script_with_a_check():
    entries = _runner_module().parse_manifest(MANIFEST.read_text(), REPO)
    assert entries, "the shipped manifest must list at least one migration"
    for entry in entries:
        assert entry.apply.is_file(), f"manifest lists a missing apply script: {entry.apply}"
        assert entry.check.is_file(), f"manifest lists a missing check script: {entry.check}"
        assert entry.check != entry.apply, "the check must be a separate program from the apply"


def test_the_shipped_manifest_survives_a_missing_trailing_newline():
    """The 2026-09-24 skip in one assertion: the final line must be read whether
    or not the file ends with a newline."""
    text = MANIFEST.read_text().rstrip("\n")
    assert _runner_module().parse_manifest(text, REPO)


def test_both_callers_make_exactly_one_runner_call_and_gate_on_its_verdict():
    for name in RUNNER_CALLERS:
        src = (DEPLOY / name).read_text()
        assert src.count("--manifest") == 1, f"{name} must make exactly one runner call"
        assert src.count("--apply") == 1, f"{name} must call the runner in apply mode"
        # A zero exit is not enough: the summary line is asserted, so a run that
        # did not apply AND check every migration can never be followed by a
        # boot, a flip, or a "ready" verdict.
        assert "applied\\+checked, 0 failed$" in src, f"{name} does not gate on the verdict"
        # Verdicts are READ by the callers, never authored: a caller that writes
        # its own "ok" line can report success the runner never claimed.
        assert "apply ok; backup ok; check ok" not in src, f"{name} fabricates a verdict line"


def _source_of(name: str) -> str:
    """The text of one box script."""
    return (DEPLOY / name).read_text()


def _assert_no_retired_invocations(name: str, source: str) -> None:
    missing = [retired for retired in RETIRED_INVOCATIONS if retired in source]
    assert not missing, f"{name} still references the retired {missing}"


def test_the_deleted_mechanisms_are_gone():
    still_present = [gone for gone in RETIRED_FILES if (DEPLOY / gone).exists()]
    assert not still_present, f"these should have been deleted by the plan: {still_present}"
    assert not (REPO / "terraform" / "user_data.sh").exists(), "terraform's own startup script is retired"
    checked = {name: _source_of(name) for name in (*RUNNER_CALLERS, "box-bootstrap.sh")} | {
        "release.yml": WORKFLOW.read_text()
    }
    for name, source in checked.items():
        _assert_no_retired_invocations(name, source)


def test_the_box_has_one_unit_and_one_port():
    assert (DEPLOY / "units" / "houses.service").is_file(), "the box's ONE app unit"
    per_side = [gone for gone in ("houses-blue.service", "houses-green.service") if (DEPLOY / "units" / gone).exists()]
    assert not per_side, f"the retired per-side units are back: {per_side}"
    assert "HOUSES_PORT=8765" in _source_of("run-instance.sh")
    # No second port anywhere in the chain — the L4 rule's target is the role.
    chain = [*sorted(DEPLOY.glob("*.sh")), DEPLOY / "units" / "houses.service", WORKFLOW]
    carries = [path.name for path in chain if "8766" in path.read_text()]
    assert not carries, f"these still carry the retired standby port: {carries}"


def test_the_allowlist_sanctions_only_the_rollout_shapes():
    dispatcher = (DEPLOY / "deploy-allowlist.sh").read_text()
    for shape in ("install-artifact.sh", "--snapshot", "--rebase", "--diagnose"):
        assert shape in dispatcher, f"the allowlist must sanction {shape}"


def _job_block(workflow: str, job: str) -> str:
    """One job's body, so an assertion about its wiring stays local to it."""
    match = re.search(rf"^  {job}:\n(.*?)(?=^  [A-Za-z#]|\Z)", workflow, re.MULTILINE | re.DOTALL)
    assert match, f"no {job!r} job in the workflow"
    return match.group(1)


def test_every_rollout_rebuilds_the_standby_and_moves_the_certificate():
    """Replace-not-repair: a rollout REBUILDS the standby instance from the
    artifact (so no box drifts and the bootstrap path is exercised every time),
    and install then runs against that fresh box."""
    workflow = WORKFLOW.read_text()
    rebuild = _job_block(workflow, "provision")
    assert "inputs.action == 'release'" in rebuild, "a release must rebuild the box"
    assert "inputs.action == 'provision'" in rebuild, "a bare rebuild must stay possible"
    assert "-replace " in rebuild, "the box is replaced, not patched"
    assert "needs: [resolve, build, provision]" in workflow, "install must follow the rebuild"
    # The instance lifecycle belongs to Terraform; traffic is moved ONLY by the
    # rules' target. A hand-run create/delete here would be the 2026-09-24 outage.
    assert "instances create" not in workflow
    assert "instances delete" not in workflow
