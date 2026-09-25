# Anti-Fragile Rollout Plan

**Status:** Plan — Phase 1 partially landed on `wip/box-provision`.
**Scope:** the external provisioning/release chain (`.github/workflows/release.yml` + `tools/deploy/*.sh` + the GCP slice). Not frontend, not the DAG library.

## Why this exists

The 2026-09-24/25 rollout from the branch failed in four distinct ways, each a failure of imperative hand-rolled orchestration:

| Incident | Root cause | Resolution |
|---|---|---|
| Provision's idempotent `delete` destroyed the **live** box before the new one was ready | the launch step deletes `houses-rebuild` unconditionally; the live box had that name | launch guard now refuses to delete a box that owns the static IP |
| Cutover's static-IP move failed (`At most one access config currently supported`) | the fresh instance keeps its auto-assigned ephemeral config; GCP allows one | cutover now deletes the ephemeral config before attaching the static |
| Fresh box's flip aborted (no `blue/.venv`) | the release only installs the standby side's venv; the snapshot script needs *a* python | `switch.sh` falls back to any installed side's venv |
| **The migration silently never ran** | `migrations.list` shipped with no trailing newline; `while IFS= read -r MIG` skips a newline-less final line — the list was comments-only to the loop, so the flip reported success with nothing executed | newline added, both loops hardened (`|| [ -n "$MIG" ]`), regression tests, live box fixed |

The migration skip is the emblematic failure: a safety-critical, fail-fast-designed mechanism was disabled by **one missing byte** and nothing verified the run — no mark, no transcript, zero seconds between stop and restart, flip completes. The fail-fast guarded only the missing-*file* case, never the missing-*entry* case. The data was unaffected only by luck (the persons backfill is a no-op on both the seed and the current DB).

The response is **a pipeline that gets it right in the first place** — not additional defensive machinery layered on after the fact (checksums, state ledgers, phased schema evolution). The unit of correctness: **migration script + its paired check + a verdict**, run on the non-live side; a wrong result rolls back by simply not promoting.

## Principles

1. **Deployments are performed by computers, not humans, in small self-contained artifacts** (Google SRE Workbook ch. 16). Google's postmortem data: a majority of incidents are triggered by binary or config pushes. Small artifacts ⇒ cheap rollback ⇒ frequent boring releases.
2. **Blue/green: two environments, routing flip, trivial rollback** (Fowler, Blue Green Deployment; SRE Workbook: blue/green = simplest canary, rollback = "a trivial reversal of the router change"). The standby is where release defects are supposed to surface — it runs the real install and the real migration against a copy, and if that goes wrong the standby is discarded, production untouched.
3. **A migration is a script plus its check.** Each migration ships a verification that asserts the migration's effect (exit non-zero when the work isn't complete). The pipeline runs the script it shipped, then runs the check; the verdict is the gate. The migration runner contract (`run-migration.sh`: dry-run / apply / backup / **verify**) already embodies this — the work is to make invocation + verdict hard requirements, tested.
4. **Infrastructure is declared, planned, and reviewed** (Terraform): plan shows every create/update/**destroy** before it happens; state = source of truth; operations dependency-ordered; immutable infrastructure.
5. **Deliberately not adopted** — the database-versioning machinery (migration checksums + applied-state ledgers, expand/contract schema evolution) that larger shared databases use. Houses has ~40 properties, a disposable standby, and minutes-scale downtime tolerance; the paired-check-on-the-standby design covers the failure modes with a fraction of the machinery. (Sources for the considered-and-rejected approaches: Liquibase changelog/checksum model; Fowler's Parallel Change.)

## Data-direction decision (settled 2026-09-25)

The restore source for any fresh box is the **trusted pre-migration backup + the migrations**, not the *current* live output: the last migrated state was produced by a rollout the user judged untrustworthy, and the migration pipeline brings the trusted data forward deterministically.

Consequences, already or to be applied:

- The seed bucket (`gs://houses-seed/latest.db`) holds the trusted seed. (`--publish`-style mechanisms that upload the *current live* DB as the next restore source — `switch.sh --publish` + the workflow publish steps from `fa0b48d` — are **superseded** and will be removed in Phase 1: they would carry an untrusted state forward.)
- The live box keeps serving its current DB (functionally identical for the applied migration); migration consistency is enforced by the pipeline's run-and-check, not by which DB file is newest.

## Current state of the branch (as of 2026-09-25)

Landed + pushed:

- `migrations.list` newline-terminated; `switch.sh` + `release.sh` loops hardened; regression tests (`test_deploy_migrations_list.py`: newline contract + newline-less final line still invoked).
- Launch guard (never delete a box owning the static IP).
- Cutover deletes the ephemeral access config before attaching the static.
- `switch.sh` venv fallback; `install-caddy.sh` chowns the ACME storage.
- Box-side: fixed `migrations.list` installed; loop replay now processes the migration path; the box SA has object-scoped write on the seed bucket (needed for later automation).

## The human approval gate for the smoke test (required, unchanged in spirit)

**The cutover — any traffic change — stays a human-gated step. The gate is the smoke evidence.**

1. `deploy-new` installs the release artifact on the standby, boots it against a **snapshot copy of the trusted DB**, and runs the smoke suite: the migrations (script + paired check) against that copy, health, `last_write` freshness, and a canary property evaluated end-to-end on real data.
2. The smoke transcript + evidence (run artifacts, the checkpoint values — including each migration's check verdict) are published with the run.
3. The `cutover` job — the only job that moves traffic — runs under `environment: production` with Required reviewers. **The reviewer's approval is the smoke gate: the cutover must not start until a human has seen the smoke evidence and approved.** This is the existing production-environment approval, made explicit about what it gates.
4. On smoke failure — including any migration check failing — no approval is possible: the chain aborts, the standby stays stopped, the live box is untouched, and the run surfaces the failing checks. Re-run or fix; the standby is disposable, so "roll it back" means discard it and rebuild.
5. Nothing in Phases 1–3 removes or automates this human checkpoint. Automation replaces everything *around* it (planning, building, installing, evaluating, rolling back); the decision to move traffic remains with a human looking at evidence.

## Phases

### Phase 1 — The migration pipeline runs what it ships, verified by the paired check (remaining work)

The migration runner's orchestration — manifest parsing, run order, verdict gating, evidence — is **tested code, not shell**. This is the direct consequence of the research finding: the missing-newline bug was shell orchestration (a `while read` over a text file) with no tests. Shell remains in exactly two places, and nothing else:

- **the deploy-key allowlist** — a security surface by design: OpenSSH's forced command runs it and it matches exact command strings; there is nothing to convert, it is a lock;
- **the fixed service-lifecycle sequence in release.sh/switch.sh** — snapshot, stop, call the runner, restart, verify, markers. These shrink to thin callers of the runner; the sequence itself is the next thing brought under test, not "gone".

- **A migration runner as a real program** (`tools/deploy/run_migrations.py`), implementing the existing `run-migration.sh` contract (dry-run / apply / backup / verify):
  - reads the manifest with a real parser — the file-shape fragility class (trailing-newline, comment filtering) is impossible;
  - for each manifest migration: run the migration's script (idempotent), then run **its paired check** (the `--verify` contract: exit non-zero when the migration's effect is incomplete);
  - one verdict line per migration; exit non-zero on any failure; the transcript is the evidence.
- **Mandatory paired check.** Every migration in the manifest must ship a check of its effect. A manifest entry without a check = invalid; the runner refuses.
- **Invocation + verdict are hard gates, on the non-live side first.** Rehearsal: the runner against the standby's DB copy — any failure aborts the release, standby stays stopped, live untouched. Flip: the same run against the live DB with prod stopped — failure restores the pre-flip snapshot and brings the old side back.
- **Test the pipeline, not just the file read.** Unit tests on the runner against a fixture DB assert: the migration ran, the paired check ran, and a deliberately skipped/incomplete migration **fails the run** (the test that would have caught the missing-newline bug — it exercises invocation and verdict, not parsing trivia).
- **Remove the superseded publish mechanism** (`switch.sh --publish`, the provision + cutover publish steps, the allowlist entry) — replaced by the trusted-seed + migration pipeline direction.
- **Acceptance:** the runner is unit-tested; a release whose standby migration check fails cannot reach the cutover gate; per-migration verdicts are in the run transcript.

### Phase 2 — Terraform the GCP layer

- Introduce `terraform/` for the GCP slice: the instance (immutable, tagged), the instance template/group or single instance as blue/green, the static IP, the firewall rules, and a **forwarding rule / load balancer as the traffic switch** — the cutover becomes a routing change instead of an IP-attach dance, and rollback = flip the route back.
- CI runs `terraform plan` in the release; the plan is reviewed before apply; **destroy of the live (routing-owned) resource is impossible by construction** — the delete of a traffic-carrying instance is a declared dependency violation, not a guard clause in bash.
- State stored off-box (GCS backend). Naming: blue/green instances get stable roles; "which box owns the IP" = `terraform show`, not forensics.
- **Acceptance:** a full re-provision never touches the serving instance; cutover/rollback is one routing command verified by the same smoke gate.

### Phase 3 — Single release artifact

- CI builds ONE deploy bundle (code + frontend dist + pinned venv + units + tools + the migrations manifest + paired checks) with a content hash; the box installs the bundle verbatim; no checkout-at-release-time (the `release.sh`/`switch.sh` per-side install and the missing-venv class disappear).
- Reproducible: same ref ⇒ same artifact (SRE principle #1).
- **Acceptance:** a fresh box reaches serving with zero per-box setup variance; artifact hash recorded in the run.

## Acceptance criteria (whole plan)

1. A rollout from a scratch box = automated through: artifact build → standby install → migration run-and-check on the standby → smoke → **human approval** → cutover → settle. Exactly one human decision.
2. A failed migration on the standby = release aborts, standby discarded, production never touched. A failed live flip = pre-flip snapshot restored, old side back.
3. The migration pipeline cannot report success without the migration's check having run and passed: the runner is a tested program (not shell), the verdict is part of the gate, and a test exercises the whole step.
4. No destructive step precedes the trusted state (the launch guard remains; the publish direction is removed).
5. Every rollout exercises the recovery path (blue/green = hot standby = the DR drill, per Fowler).

## Operations & troubleshooting

- **Humans: the operator key is full admin, unchanged.** The unrestricted key in the instance `ssh-keys` metadata (locally `~/.ssh/houses_operator`): normal shell + passwordless sudo. This is the troubleshooting path — the one that resolved the 2026-09 incidents (flip transcripts, journalctl, sqlite3 on the live DB, dry-runs). The deploy-key allowlist does NOT constrain it; it constrains automation only.
- **CI: least privilege.** The deploy key runs exactly the allowlist shapes. Read-only diagnostics are already sanctioned: `switch.sh --diagnose` (box-state dump: tooling shas, migrations.list, ACTIVE/PREVIOUS, units, snapshots, live DB + WAL, side layouts, journal tails) and `journalctl`. A new sanctioned command = an explicit, reviewed change to `deploy-allowlist.sh` + the sudoers (mechanism: `install-deploy-allowlist.sh`).
- **Evidence over state.** The runner's per-migration verdicts + transcripts and the release logs are the same artifacts humans and automation read — "what did the migration do" is a log line, never a reconstruction.
- **Reproducibility (Phase 3).** The installed artifact hash answers "what is this box" — diagnosis compares hashes instead of re-deriving from setup scripts.
- **Declared infra (Phase 2).** `terraform show`/`plan` = what should exist; drift vs actual is a finding, not a mystery.
- **Escape hatches.** Serial console (`gcloud compute instances get-serial-port-output`) + startup-script logs when SSH is unreachable; gcloud for static-IP owner and instance status.
- **Recovery.** Pre-flip snapshot + `switch.sh --rollback`; beyond that the standby is disposable — rebuild it.

## Risks / tradeoffs

- **Terraform state**: needs an off-box backend + a decision on who applies (CI SA with scoped perms).
- **Blue/green cost**: one extra instance (e2-micro). Acceptable; the old chain already peaked at two during cutover.
- **Paired-check quality**: a weak check (e.g. always passes) is the new silent-skip risk — that's exactly what the release-abort paths and the fixture tests are for, and the human reviewer sees the verdicts.
- **A migration already applied on the live DB**: the paired check treats "nothing left to do" as pass — the backfill's `rows remapped: 0` is exactly that, so an already-migrated DB passes cleanly.

## Sources

- Google SRE Workbook, ch. 16 *Canarying Releases* — https://sre.google/workbook/canarying-releases/
- Martin Fowler, *Blue Green Deployment* — https://martinfowler.com/bliki/BlueGreenDeployment.html
- HashiCorp, *What is Terraform* — https://developer.hashicorp.com/terraform/intro
- Considered and deliberately not adopted: Liquibase changelog/checksum model — https://docs.liquibase.com/concepts/changeset.html ; Fowler / Danilo Sato, *Parallel Change* (expand/contract) — https://martinfowler.com/bliki/ParallelChange.html