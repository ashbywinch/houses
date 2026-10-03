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
    assert (
        "needs: [resolve, build, provision, image-stale]" in workflow
    ), "install must follow the rebuild (and the image-drift gate)"
    # The instance lifecycle belongs to Terraform; traffic is moved ONLY by the
    # rules' target. A hand-run create/delete here would be the 2026-09-24 outage.
    assert "instances create" not in workflow
    assert "instances delete" not in workflow


def test_traffic_moving_jobs_fail_closed_without_the_human_approval():
    """2026-10-02: a flip ran without any approval because the production
    environment had NO protection rules — GitHub autopassed the declared
    environment gate. Every traffic-moving job must now refuse BEFORE any box
    or traffic operation unless the dispatch carried approval=approved; the
    environment gate is a second lock, never the only one."""
    workflow = WORKFLOW.read_text()
    # The dispatch input must exist and default FAIL-CLOSED (no default that
    # means "go").
    inputs = workflow[workflow.index("workflow_dispatch:") : workflow.index("jobs:")]
    assert "approval:" in inputs
    assert "default: 'pending'" in inputs
    assert "options: ['pending', 'approved']" in inputs
    for job in ("cutover", "flip"):
        block = _job_block(workflow, job)
        gate = block.index("- name: Human approval gate")
        assert "inputs.approval != 'approved'" in block, f"{job} loses the gate condition"
        assert "approval=approved" in block, f"{job} loses the re-dispatch instruction"
        # NO box or traffic operation may precede the gate: a guard that sits
        # after the first ssh or set-target is not a guard.
        for op in ("forwarding-rules", "switch.sh", "install-artifact.sh",
                   "--snapshot", "--restore", "--rebase", "--smoke-relay", "--role "):
            idx = block.find(op)
            assert idx == -1 or gate < idx, f"{job}: {op!r} precedes the approval gate"


def test_no_job_may_both_restore_a_db_and_flip_traffic():
    """2026-10-02: recover used to restore the DB AFTER its approval gate and
    then flip — the box that went live was never the box the human approved.
    The invariant: restore is UNGATED PREP onto the standby (it becomes the
    review surface), and the gated `flip` makes the already-restored box live
    without touching the DB. No single job may contain both a restore and a
    rules move."""
    workflow = WORKFLOW.read_text()
    for job in ("cutover", "flip", "recover"):
        block = _job_block(workflow, job)
        restores = "--restore " in block
        flips = "forwarding-rules" in block or "set-target" in block
        assert not (restores and flips), f"{job} both restores a DB and moves traffic"
    # recover is ungated PREP and it makes the box FINAL: production role
    # URL (OAuth callbacks by design), restore, serve + verify the surface.
    rec = _job_block(workflow, "recover")
    assert "environment: production" not in rec, "prep must not be gated"
    assert "Human approval gate" not in rec
    assert "--restore " in rec
    # The app code rides the prep too: the box must be FINAL — the
    # host-aware OAuth fix is what lets the reviewer sign in on the review
    # hostname while the box already carries its production role URL.
    assert "install-artifact.sh" in rec
    assert "needs.build.outputs.artifact" in rec
    assert "needs: [resolve, build]" in WORKFLOW.read_text()
    assert "switch.sh --public-url https://houses.blueumbrella.net" in rec, "the box is FINAL before approval"
    assert "houses-smoke.blueumbrella.net" in rec, "the reviewer views it at the smoke hostname"
    assert "forwarding-rules" not in rec and "set-target" not in rec
    # The gated traffic movers never restore: the box they promote is the one
    # that was reviewed.
    for job in ("cutover", "flip"):
        block = _job_block(workflow, job)
        assert "--restore " not in block, f"{job} must never restore after its gate"
        assert "environment: production" in block, f"{job} must declare the environment gate"


def test_flip_changes_nothing_on_the_approved_box():
    """2026-10-03: the box must be exactly what the human approved. Every
    box-side action (install, tooling, role URL, restart, DB op) happens in
    the ungated prep; the gated flip only retires the smoke relay on the
    abandoned OWNER and moves the traffic rules. If a flip step can ssh to
    the standby at all, the invariant is one edit away from being broken."""
    flip = _job_block(WORKFLOW.read_text(), "flip")
    approved_box_ops = ("STANDBY_IP", "install-artifact.sh", "switch.sh --role",
                        "switch.sh --public-url", "switch.sh --restore")
    for op in approved_box_ops:
        assert op not in flip, f"flip touches the approved box: {op!r}"
    # It does still do its actual job: owner relay retirement + rules move.
    assert "needs.resolve.outputs.owner_ip" in flip
    assert "switch.sh --smoke-relay off" in flip
    assert "forwarding-rules set-target" in flip


def test_the_bake_retries_capacity_with_exponential_backoff():
    """2026-10-02: the bake's fixed 3×120s retry sat inside a continuous
    pool-exhaustion outage (six instant rejections across two runs in 11
    minutes) and never had a chance — the ~5-minute horizon is an order of
    magnitude shorter than the capacity-clearing cadence. The capacity retry
    must be exponential over a ~55-minute window and must still fail
    immediately on any NON-capacity error. ONE retry mechanism — no
    dispatch-level retry on top."""
    bake = _job_block(WORKFLOW.read_text(), "bake")
    assert "does not have enough resources|capacity" in bake, "only the capacity class is retried"
    assert 'while [ "$attempt" -lt 6 ]' in bake, "six attempts"
    assert "BACKOFF=$((BACKOFF * 2))" in bake, "exponential backoff"
    assert '[ "$BACKOFF" -gt 1500 ]' in bake, "the backoff is capped"
    assert "timeout-minutes: 90" in bake, "the cap must clear the ~55-min sleep sum + apply overhead"


def test_no_step_chains_two_deploy_key_commands():
    """2026-10-03: the deploy-key allowlist matches the WHOLE remote command
    string exactly, so `sudo switch.sh --public-url X && sudo switch.sh
    --restore Y` in ONE ssh matches nothing and is a SILENT NO-OP — exit 0,
    nothing run. That is how the recover prep "restored" nothing for 10
    minutes and how the cutover's rebase never restored; both hid behind a
    green step. One sanctioned shape per ssh call, always."""
    workflow = WORKFLOW.read_text()
    for chained in ("&& sudo /opt/houses", "; sudo /opt/houses", "&& sudo -n /opt/houses"):
        assert chained not in workflow, f"chained deploy-key command (silent no-op): {chained!r}"
    # The two multi-verb flows must still do BOTH things — in separate calls.
    rec = _job_block(workflow, "recover")
    assert "switch.sh --public-url https://houses.blueumbrella.net" in rec
    assert "switch.sh --restore $SOURCE" in rec
    cut = _job_block(workflow, "cutover")
    assert "switch.sh --public-url https://houses.blueumbrella.net" in cut
    assert "switch.sh --rebase " in cut



def test_the_deploy_key_is_written_before_its_first_use_in_a_job():
    """2026-10-03: the recover prep's install step ssh'd with
    `-i /tmp/deploy.key` before any step in that job had written the key —
    'Identity file … not accessible' → permission denied, 4 seconds in. The
    key persists across a job's steps, but only if something wrote it first;
    a new step inserted above the writer silently breaks the whole job."""
    workflow = WORKFLOW.read_text()
    problems = []
    for job in ("recover", "flip", "cutover", "install", "provision", "rollback"):
        steps = re.split(r"\n      - name: ", _job_block(workflow, job))
        first_use = next((i for i, s in enumerate(steps) if "-i /tmp/deploy.key" in s), None)
        if first_use is None:
            continue
        if not any("> /tmp/deploy.key" in s for s in steps[: first_use + 1]):
            problems.append(f"{job}: {steps[first_use].splitlines()[0]!r} uses the key before it is written")
    assert not problems, problems
