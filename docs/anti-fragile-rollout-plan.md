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

The research (see [Sources](#sources)) says the tooling class itself — bash orchestration of irreversible cloud actions with no plan, no state, no verification — is the recurring root cause, not this bug. This plan replaces the class.

## Principles

1. **Deployments are performed by computers, not humans, in small self-contained artifacts** (Google SRE Workbook ch. 16). Google's postmortem data: a majority of incidents are triggered by binary or config pushes. Small artifacts ⇒ cheap rollback ⇒ frequent boring releases.
2. **Blue/green: two environments, routing flip, trivial rollback; schema changes ship separately and first** (Fowler, Blue Green Deployment; SRE Workbook: blue/green = simplest canary, rollback = "a trivial reversal of the router change").
3. **Expand/contract for backward-incompatible changes** (Fowler, Parallel Change): expand (support both), migrate (all consumers), contract (remove the old). Database refactoring is the canonical application.
4. **Infrastructure is declared, planned, and reviewed** (Terraform): plan shows every create/update/**destroy** before it happens; state = source of truth; operations dependency-ordered; immutable infrastructure.
5. **Migrations are a versioned ledger with checksums and preconditions** (Liquibase): applied-state tracked in the database, unique identifiers per changeset, preconditions before unrecoverable operations — not a text file parsed by a shell loop.

## Data-direction decision (settled 2026-09-25)

The restore source for any fresh box is the **trusted pre-migration backup + the expand/contract migrations**, not the *current* live output: the last migrated state was produced by a rollout the user judged untrustworthy, and the migration ledger exists to bring the trusted data forward deterministically.

Consequences, already or to be applied:

- The seed bucket (`gs://houses-seed/latest.db`) holds the trusted seed. (`--publish`-style mechanisms that upload the *current live* DB as the next restore source — `switch.sh --publish` + the workflow publish steps from `fa0b48d` — are **superseded** and will be removed in Phase 1: they would carry an untrusted state forward.)
- The live box keeps serving its current DB (functionally identical for the applied migration); migration consistency is enforced by the ledger, not by which DB file is newest.

## Current state of the branch (as of 2026-09-25)

Landed + pushed:

- `migrations.list` newline-terminated; `switch.sh` + `release.sh` loops hardened; regression tests (`test_deploy_migrations_list.py`: newline contract + newline-less final line still invoked).
- Launch guard (never delete a box owning the static IP).
- Cutover deletes the ephemeral access config before attaching the static.
- `switch.sh` venv fallback; `install-caddy.sh` chowns the ACME storage.
- Box-side: fixed `migrations.list` installed; loop replay now processes the migration path; the box SA has object-scoped write on the seed bucket (needed for later automation).

## The human approval gate for the smoke test (required, unchanged in spirit)

**The cutover — any traffic change — stays a human-gated step. The gate is the smoke evidence.**

1. `deploy-new` installs the release artifact on the standby, boots it against a **snapshot copy of the trusted DB**, and runs the smoke suite: health, `last_write` freshness, migration-applied-state vs the ledger, and a canary property evaluated end-to-end on real data.
2. The smoke transcript + evidence (run artifacts, the checkpoint values) are published with the run.
3. The `cutover` job — the only job that moves traffic — runs under `environment: production` with Required reviewers. **The reviewer's approval is the smoke gate: the cutover must not start until a human has seen the smoke evidence and approved.** This is the existing production-environment approval, made explicit about what it gates.
4. On smoke failure, no approval is possible: the chain aborts, the standby stays stopped, the live box is untouched, and the run surfaces the failing checks. Re-run or fix — never proceed on a failed smoke.
5. Nothing in Phases 1–4 removes or automates this human checkpoint. Automation replaces everything *around* it (planning, building, installing, evaluating, rolling back); the decision to move traffic remains with a human looking at evidence.

## Phases

### Phase 1 — Migration integrity (remaining work)

- **Applied-state ledger + checksums.** Add a `migrations_applied` table (migration id, content checksum, applied_at); `run-migration.sh` records it after a verified apply; the flip's precondition AND its post-verify compare the ledger against the shipped manifest — any mismatch (missing entry, changed checksum, skipped run) aborts the flip. The missing-newline class dies for good: the loop result is *checked against the database*, not trusted from a file read.
- **Remove the superseded publish mechanism** (`switch.sh --publish`, the provision + cutover publish steps, the allowlist entry) — replaced by the ledger + trusted-seed direction.
- **Restore the bucket to the trusted seed** if needed before any next provision.
- **Acceptance:** a flip with any unapplied/changed migration fails loudly before traffic moves, with the snapshot restored; a unit test covers a tampered manifest.

### Phase 2 — Terraform the GCP layer

- Introduce `terraform/` for the GCP slice: the instance (immutable, tagged), the instance template/group or single instance as blue/green, the static IP, the firewall rules, and a **forwarding rule / load balancer as the traffic switch** — the cutover becomes a routing change instead of an IP-attach dance, and rollback = flip the route back.
- CI runs `terraform plan` in the release; the plan is reviewed before apply; **destroy of the live (routing-owned) resource is impossible by construction** — the delete of a traffic-carrying instance is a declared dependency violation, not a guard clause in bash.
- State stored off-box (GCS backend). Naming: blue/green instances get stable roles; "which box owns the IP" = `terraform show`, not forensics.
- **Acceptance:** a full re-provision never touches the serving instance; cutover/rollback is one routing command verified by the same smoke gate.

### Phase 3 — Single release artifact

- CI builds ONE deploy bundle (code + frontend dist + pinned venv + units + tools + the migrations manifest) with a content checksum; the box installs the bundle verbatim; no checkout-at-release-time (the `release.sh`/`switch.sh` per-side install and the missing-venv class disappear).
- Reproducible: same ref ⇒ same artifact (SRE principle #1).
- **Acceptance:** a fresh box reaches serving with zero per-box setup variance; artifact hash recorded in the run.

### Phase 4 — Expand/contract migrations + canary-style evaluation gate

- Migrations ship as expand/contract: schema/data changes that support old and new run in their own release step **before** the app release, verified, with the ledger as the rollback point (Fowler). The DAG recompute remains a background settle after cutover, bounded by the gateway (quota short-circuit, Retry-After, keep-walk fallbacks).
- The smoke suite becomes a canary-style evaluation with attributable metrics: standby vs live comparison on health, `last_write`, error rate, and the canary property's end-to-end values over a settle window; thresholds from real data; failure ⇒ auto-rollback by routing (still initiated by the human gate in Phase ≤3, then by the evaluation once the thresholds earn trust).
- **Acceptance:** a bad release is caught on the standby or in the canary window before full traffic, and rollback = one routing flip.

## Acceptance criteria (whole plan)

1. A rollout from a scratch box = automated through: artifact build → standby install → smoke → **human approval** → cutover → settle. Exactly one human decision.
2. Rollback = routing flip, ≤ a few minutes, via the pre-flip snapshot + ledger.
3. A migration can never be silently skipped: the ledger + checksum blocks the flip, with evidence.
4. No destructive step precedes a verified, trusted state (the launch guard remains; the publish direction is removed).
5. Every rollout exercises the recovery path (blue/green = hot standby = the DR drill, per Fowler).

## Risks / tradeoffs

- **Terraform state**: needs an off-box backend + a decision on who applies (CI SA with scoped perms).
- **Blue/green cost**: one extra instance (e2-micro). Acceptable; the old chain already peaked at two during cutover.
- **Canary metrics on a small property set**: 40ish properties ⇒ the evaluation window/thresholds need real settle data before they replace the human gate. The human gate stays until the evaluation proves itself.
- **Ledger on an existing DB**: the `migrations_applied` bootstrap must treat the current state as "already applied, checksum verified in dry-run" — the backfill's `rows remapped: 0` proves it.

## Sources

- Google SRE Workbook, ch. 16 *Canarying Releases* — https://sre.google/workbook/canarying-releases/
- Martin Fowler, *Blue Green Deployment* — https://martinfowler.com/bliki/BlueGreenDeployment.html
- Martin Fowler / Danilo Sato, *Parallel Change* — https://martinfowler.com/bliki/ParallelChange.html
- HashiCorp, *What is Terraform* — https://developer.hashicorp.com/terraform/intro
- Liquibase, *What is a Changelog* — https://docs.liquibase.com/concepts/changeset.html