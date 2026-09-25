# Anti-Fragile Rollout Plan

**Status:** Plan — Phase 1 partially landed on `wip/box-provision`.
**Scope:** the external provisioning/release chain (`.github/workflows/release.yml` + `tools/deploy/*` + the GCP slice). Not frontend, not the DAG library.

## Requirements

1. **Simple.**
2. **Obviously correct** in its implementation — it should be obvious why it can't go wrong.
3. **Anti-fragile.**
4. **Easy to find, read, understand and use for new agents with no context.**

## Why this exists

The 2026-09-24/25 rollout from the branch failed in four distinct ways, each a failure of imperative hand-rolled orchestration:

| Incident | Root cause | Resolution |
|---|---|---|
| Provision's idempotent `delete` destroyed the **live** box before the new one was ready | the launch step deletes `houses-rebuild` unconditionally; the live box had that name | launch guard refuses to delete a box that owns the static IP; Phase 2 makes it structurally impossible |
| Cutover's static-IP move failed (`At most one access config currently supported`) | the fresh instance keeps an auto-assigned ephemeral config; GCP allows one | cutover deletes the ephemeral config before attaching the static; Phase 2 replaces IP moves with a route flip |
| Fresh box's flip aborted (no `blue/.venv`) | the release only installs the standby side's venv | `switch.sh` falls back to any installed side's venv; Phase 3 removes checkout-at-release entirely |
| **The migration silently never ran** | `migrations.list` shipped with no trailing newline; `while IFS= read -r MIG` skips a newline-less final line — the list was comments-only to the loop, so the flip reported success with nothing executed | newline + loop hardening landed; Phase 1 replaces the file-read with a real parser and a mandatory apply-then-check verdict |

The migration skip is the emblematic failure: a safety-critical, fail-fast-designed mechanism was disabled by **one missing byte** and nothing verified the run. The response is a runner that gets it right in the first place — not defensive machinery around it.

## Decisions (answers to the implementation-review questions)

Each review question, its decision, and one line of why.

| # | Question | Decision | Why |
|---|---|---|---|
| Q1 | How does a manifest entry "ship its paired check"? | `migrations.list` = one line per migration: `<apply-script> <check-script>` (whitespace-separated). The parser requires two existing paths per line; an entry without a check **cannot parse**. | The parser enforces the invariant — a migration without a check is refused at the first step, by construction. |
| Q2 | Check = separate run or combined `--verify`? | Two separate subprocesses per migration, in order: apply (`<apply-script> --db <db> --apply --backup`), then check (`<check-script> --db <db>`, exit 0 iff complete). | The check must be independent of the code that just wrote — a same-process `--verify` cannot see that the apply did nothing. |
| Q3 | Caller contract | `release.sh` (rehearsal) and `switch.sh` (flip) each make **one** runner call: `run_migrations.py --manifest /opt/houses/migrations.list --db <db> --apply`; non-zero exit = abort (release stops; flip restores snapshot + old side). The runner is the only manifest reader; the old grep guards delete. | One authority, one code path; callers cannot mis-invoke a migration they never parse. |
| Q4 | Who writes the seed after `--publish` removal? | `--publish` (automatic, pre-delete, current-state) is removed. The seed is written **only** by the explicit `seed-box.sh` run — a deliberate human decision that the current state is trusted (post-settle, backed up first). Every fresh box records the seed object id + timestamp it restored, as a bootstrap log line. | The restore source is always a human-validated snapshot; automation can never silently promote an untrusted state. |
| Q5 | Terraform backend + apply authority | New bucket `houses-tfstate`; the CI SA gets object-scoped admin there. `terraform plan` + `apply` run in the release's provision job, replacing the gcloud commands — automated, exactly like today's launch; plan output is a run artifact. The human gate stays only on cutover. | Apply = building infra (same as the gcloud launch today); the gate = traffic. No second human checkpoint. |
| Q6 | First Terraform adoption | `terraform import` the live instance, static IP, and firewall first (no recreation); then add the forwarding rule + standby instance in the same config; cut over on the **next** rollout. Traffic-owned resources get `prevent_destroy`. Names: `houses` (the traffic owner, stable) + `houses-standby` (the rolling one) — the standby name can never collide with the owner. | Import-first adoption touches nothing live; `prevent_destroy` makes the live-delete class a plan error, not a guard clause. |
| Q7 | Phase-2 cutover data handoff | Cutover job, before the route flip: fresh consistent snapshot of the live DB → restore onto the standby → run the runner (idempotent) → flip route → verify. Executed as one sanctioned `switch.sh --rebase` command. | The standby's release-time DB copy is stale by design; the rebase makes the flip data-fresh at the moment traffic moves, bounded by the minutes-scale downtime tolerance. |

Remaining defaults, decided:

| Question | Decision |
|---|---|
| Which `run-migration.sh` guarantees survive? | All of them, ported into `run_migrations.py`: space gate (DB + 1 GiB, `HOUSES_MIGRATION_MIN_FREE_BYTES`), dry-run-before-apply, per-migration `<db>.pre-<migration>` backup, post-apply chown/chmod. `run-migration.sh` and its test file are deleted. |
| How does smoke evidence reach the approver? | The ssh-relayed runner verdicts and the job log **are** the published evidence — one verdict line per migration is the checkpoint value. No artifact upload step. |
| What happens to the tests that pin the shell loops? | `test_deploy_migrations_list.py` + `test_deploy_run_migration.py` are rewritten to pin the runner contract (manifest parse, apply→check, verdicts, refusals) and the new caller invocation shapes; shell-loop-pinning assertions are deleted. |
| L4 or L7 at the load balancer? | **L4 passthrough** — the static IP becomes the forwarding-rule target; Caddy stays on the box; TLS, cert handling, and the smoke-host routing are unchanged. |
| Phase-3 bundle transport | The box fetches the bundle from GCS (`gs://houses-artifacts/<sha>.tar.gz`) via one new sanctioned `install-artifact.sh <object>` allowlist shape; the ref remains only for tooling. (The pinned-venv bundle exceeds the ssh-stdin ceiling.) |
| Smoke suite contents | Existing release.sh checks + per-migration runner verdicts + `last_write` freshness; "canary property" = the existing property-detail check. No new metrics tooling. |
| `--dry-run` mode | Stays as a troubleshooting mode with the same space-gate semantics; rehearsal and flip always use `--apply` (they must write and check). |

## The runner (Phase 1)

- `tools/deploy/run_migrations.py` — stdlib only, tested. Modes: `--dry-run` (read-only report) and `--apply` (space gate → per migration: dry-run report, apply+backup, **independent check**, one verdict line → exit non-zero on any failure).
- Manifest: `migrations.list`, one `<apply-script> <check-script>` pair per line. The parser rejects malformed lines, unknown paths, duplicate entries, and a final line without a newline — the file-shape fragility class cannot parse.
- Single authority: `/opt/houses/migrations.list`, shipped by the release, referenced by both callers and by the runner. No second manifest.
- Verdict evidence is the transcript: `run-migrations-<timestamp>.log`, root-owned, pruned newest-32; `switch.sh --diagnose` tails it.
- Tests: the real backfill `scripts/backfill_person_ids.py` + its new `scripts/backfill_person_ids.check.py` against a seeded temp DB — apply ran, check ran, a deliberately skipped/incomplete migration fails the run; parser failure cases; verdict lines present.

## The human approval gate for the smoke test (required, unchanged)

**The cutover — any traffic change — stays a human-gated step. The gate is the smoke evidence.**

1. `deploy-new` installs the release on the standby, boots it against a snapshot copy of the trusted DB, and runs the smoke suite: the migrations with their checks, health, `last_write` freshness, and the existing property check — all surfaced as verdict lines in the run log.
2. The `cutover` job — the only job that moves traffic — runs under `environment: production` with Required reviewers. **The reviewer's approval is the smoke gate: the cutover must not start until a human has seen the evidence and approved.**
3. On any smoke failure — including any migration check — no approval is possible: the chain aborts, the standby stays stopped, the live box is untouched.
4. Nothing in Phases 1–3 removes or automates this checkpoint. Automation replaces everything around it; the decision to move traffic remains with a human looking at evidence.

## Phases

### Phase 1 — Migration pipeline (runner + manifest + verdicts)
Build `run_migrations.py` + the `<apply> <check>` manifest + tests; delete `run-migration.sh` and its test file; make `release.sh`/`switch.sh` single-runner-call callers; delete the `--publish` mechanism and its allowlist/workflow sites; bootstrap records the seed object identity it restored.

### Phase 2 — Terraform the GCP layer
Import the live resources; add the forwarding rule + `houses-standby`; `prevent_destroy` on `houses` and the static IP; CI plan+apply in the provision job; cutover = `--rebase` (fresh snapshot → standby → runner → route flip) in the gated job; rollback = flip the route back.

### Phase 3 — Single release artifact
CI builds one bundle (code + dist + pinned venv + units + tools + manifest + checks) with a sha256; the box fetches it from GCS via `install-artifact.sh`; `/opt/houses/ARTIFACT` records the hash; fresh boxes install the bundle instead of cloning; `--diagnose` prints the hash.

## Operations & troubleshooting

- **Humans: the operator key is full admin, unchanged.** The unrestricted instance-metadata key (locally `~/.ssh/houses_operator`): shell + passwordless sudo. It is the troubleshooting path; the allowlist constrains automation, not humans.
- **CI: least privilege.** The deploy key runs exactly the allowlist shapes; read-only diagnostics = `switch.sh --diagnose` + journalctl. New sanctioned commands = explicit, reviewed allowlist + sudoers changes.
- **Evidence over state.** Runner verdicts + transcripts + release logs are the same artifacts humans and automation read.
- **Reproducibility (Phase 3).** The installed artifact hash answers "what is this box".
- **Declared infra (Phase 2).** `terraform show`/`plan` = what should exist; drift is a finding, not a mystery.
- **Escape hatches.** Serial console + startup-script logs when SSH is unreachable; gcloud for static-IP owner and instance status.
- **Recovery, by layer.** Rehearsal fails → release aborts, side stays stopped (today's shared box: the other side is NOT the disposable path). Flip fails → pre-flip snapshot + old side. Box broken → re-provision from the trusted seed (the box is disposable via provision). Phase 2's separate standby instance = the disposable one: delete, rebuild from the artifact, re-run smoke.

## Acceptance criteria

1. A rollout from a scratch box = automated through: artifact/build → standby install → migrations-with-checks → smoke → **human approval** → cutover → settle. Exactly one human decision.
2. A failed rehearsal = release aborts, standby untouched-by-promotion, production never touched. A failed flip = pre-flip snapshot restored, old side back.
3. The migration pipeline cannot report success without every migration's check having run and passed — enforced by the parser and the verdict gate, and proven by fixture tests.
4. No destructive step precedes the trusted state (launch guard; phase-2 `prevent_destroy`; the seed is human-validated).
5. Every rollout exercises the recovery path (blue/green = hot standby = the DR drill).

## Risks / tradeoffs

- Terraform state: off-box backend (`houses-tfstate`) created once by the operator; the CI SA gets scoped perms there.
- +1 instance for the two-instance layout (e2-micro), replacing the current peak-of-two during cutover.
- Paired-check quality is the load-bearing risk: a weak check is the new silent-skip. Mitigated by the verdict gate, the fixture tests, and the human reviewer seeing the verdicts.
- The Phase-2 cutover window (snapshot → rebase → route flip) is minutes of downtime by design; accepted.

## Sources

- Google SRE Workbook, ch. 16 *Canarying Releases* — https://sre.google/workbook/canarying-releases/
- Martin Fowler, *Blue Green Deployment* — https://martinfowler.com/bliki/BlueGreenDeployment.html
- HashiCorp, *What is Terraform* — https://developer.hashicorp.com/terraform/intro
- Considered and deliberately not adopted: Liquibase changelog/checksum model — https://docs.liquibase.com/concepts/changeset.html ; Fowler / Danilo Sato, *Parallel Change* — https://martinfowler.com/bliki/ParallelChange.html