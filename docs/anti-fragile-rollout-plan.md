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
| Provision's idempotent `delete` destroyed the **live** box before the new one was ready | the launch step deletes `houses-rebuild` unconditionally; the live box had that name | the traffic owner is whoever the forwarding rule targets; the workflow refuses to rebuild the targeted instance |
| Cutover's static-IP move failed (`At most one access config currently supported`) | the fresh instance keeps an auto-assigned ephemeral config; GCP allows one | the static IP never touches an instance; it is an L4 forwarding rule's address, and the flip changes the rule's target |
| Fresh box's flip aborted (no `blue/.venv`) | the release only installs the standby side's venv | boxes install one artifact (venv included) fetched from GCS by the instance's own identity |
| **The migration silently never ran** | `migrations.list` shipped with no trailing newline; `while IFS= read -r MIG` skips a newline-less final line — the list was comments-only to the loop, so the flip reported success with nothing executed | a real parser in a tested runner + mandatory apply-then-check verdicts |

## Architecture

```
                     static IP (houses-static, regional)
                                │  address of
                                ▼
                  L4 forwarding rule (houses-l4, ports 80/443)   ← data plane
                                │  target = the active instance
                                ▼
      ┌────────────────────┐    ┌──────────────────────┐
      │ houses             │    │ houses               │
      │ (currently active) │    │ (currently standby)  │   control plane:
      │ serves via Caddy   │    │ built each rollout   │
      │ on 80/443          │    │ ephemeral ext IP =   │
      └────────────────────┘    │ SSH only            │
                                └──────────────────────┘
          ssH / verifies                ssH / runs migrations
```

Four invariants, each stated so it is obvious why the design cannot go wrong:

1. **The static IP belongs to the forwarding rule and nothing else.** Instance access configs are ephemeral and for SSH only (control plane); the data plane is `houses-l4`, whose `target` is a zonal `google_compute_target_instance`. A rollout changes the rule's target — an IP is never attached to or detached from an instance, so the access-config failure class cannot occur. Caddy stays on the box (L4 passthrough, TLS unchanged).
2. **Boxes hold no secrets.** Each instance runs with its GCP service account (`houses-box-deploy`); every gsutil/gcloud call uses the metadata-server identity. Metadata carries only public keys (`ssh-keys` pubkeys) and public vars (`PROVISION_REF`, `PROVISION_ARTIFACT`). No key file exists on a box and none can appear in Terraform state.
3. **Migrations run only on the standby's DB copy, while that DB is quiescent.** Rehearsal at install; a fresh snapshot of the owner's DB is pulled, restored, and re-checked immediately before the flip — all with the standby app stopped. The owner's data is never migrated on, and no step writes the standby's DB while the runner operates on it.
4. **Exactly one human decision: the cutover approval.** Everything else in the rollout is automated; the gate is the smoke evidence.

**Ownership and the rebuild guard.** The rule target is the only "who is live" fact. Instances are fixed resources (`houses` + `houses-standby`) whose roles rotate with the target. The workflow reads the target before **any** destructive step and refuses to rebuild the targeted instance — the launch-guard pattern, with the rule as the source of truth. The address keeps `prevent_destroy` (it never changes); instances cannot, because after a flip the protected resource has moved.

## The runner

`tools/deploy/run_migrations.py` — stdlib, tested, the only reader of the migration manifest:

```
run_migrations.py --manifest <path> --db <path> --scripts-dir <abs side checkout> --python <abs venv python> [--apply | --dry-run]
```

- **Paths and interpreter are explicit caller inputs.** The manifest holds relative `<apply-script> <check-script>` pairs resolved against `--scripts-dir`, so the same shipped manifest serves both checkouts with no hidden convention; `--python` names the side's `.venv/bin/python` (migrations import `houses.*`). The runner validates both resolve and exist, and is stdlib because it only orchestrates subprocesses.
- **Manifest format:** one line per migration, whitespace-separated paths; the parser rejects malformed lines, unknown or duplicate paths, duplicate apply-script basenames, and a final line without a newline — the file-shape failure class cannot parse.
- **`--apply`:** space gate (DB + 1 GiB, `HOUSES_MIGRATION_MIN_FREE_BYTES`) → per migration: dry-run report → apply + backup → **independent check** → one verdict line → summary → exit non-zero on any failure. `--dry-run` = read-only report with the same space-gate semantics.

**Check contract (the migration's paired check).** A check is a program invoked by the runner as `<python> <check-script> --db <db>`, opens the DB **read-only**, and exits 0 iff the migration's effect is complete. It must not modify the DB. The backfill's check = `scripts/backfill_person_ids.check.py`, the same "rows remapped == 0" scan the migration's `--verify` performs today, as a standalone read-only script. The check is a separate process so it cannot vouch for the code that just wrote.

**Backup naming.** The pre-migration backup is `<db>.pre-<apply-script-basename>` (e.g. `houses.db.pre-backfill_person_ids.py`). Unique per run by the parser's duplicate-basename rejection.

**Verdict format (load-bearing: the release guard, `--diagnose`, the tests, and the human gate read exactly this):**

```
== run_migrations <iso-ts> manifest=<path> db=<path> mode=apply
migration <apply-basename>: apply ok; backup ok; check ok     # or: apply FAILED: <err> / check FAILED: <err>
migrations: <N> applied+checked, <M> failed
```

The runner prints exactly one `migration …` line per manifest entry and one summary line, then exits 0 iff the summary ends `0 failed`. Callers and `--diagnose` match the summary line's exact text; tests assert both shapes.

**Callers make exactly one runner call.** `release.sh` (rehearsal against the standby DB copy) and `switch.sh` (the flip, live DB, prod stopped — until Phase 2 removes the per-box flip) each invoke the runner once; non-zero = abort (release stops; flip restores the pre-flip snapshot and the old side). All of the deleted `run-migration.sh`'s guarantees live inside the runner (space gate, backup, dry-run-first, post-apply chown/chmod).

**Evidence:** one `run-migrations-<timestamp>.log` per run, root-owned, pruned newest-32; `switch.sh --diagnose` tails it.

**Tests:** the real backfill + `backfill_person_ids.check.py` against a seeded temp DB — apply ran, check ran, a deliberately skipped or incomplete migration fails the run; the verdict forms above appear; parser failure cases.

## Artifacts and bootstrap

- **The artifact is a content-addressed object in GCS** — `gs://houses-artifacts/<sha256>.tar.gz` — built by a `build-artifact` CI job: pinned `uv sync` + frontend `npm ci && npm run build` into one tarball (code, `frontend/dist`, `.venv`, units, tools, `migrations.list`, checks); the sha256 of the bundle is the object key. GCS over the GitHub artifact store is a consequence of who consumes it: the **box**, at first boot and install, by its instance SA via the metadata server — no token, no secret, no size-capped ssh re-pipe.
- **Install:** one new sanctioned shape, `sudo /opt/houses/install-artifact.sh <object>` (charset-validated). It fetches with the instance SA, verifies the sha256 equals the key, unpacks over the standby's checkout, runs the runner (rehearsal) and the smoke locally (127.0.0.1), stops the standby app, and streams verdicts. The stdin-dist pipe and per-side git checkouts disappear; tooling ships inside the artifact.
- **Fresh box:** `PROVISION_ARTIFACT` in metadata (public) names the object; bootstrap step 2 fetches and unpacks it with the instance SA. The artifact hash answers "what is this box": `/opt/houses/ARTIFACT` records `<sha256>` + `<git ref>`; `--diagnose` prints both.
- **Seed provenance:** the bootstrap writes `/opt/houses/SEED` — `object=<seed object> etag=<etag> restored_at=<iso> rows=<n>` (values it already has during restore) — so "what did this box start from" is a file, printed by `--diagnose`.

## Cutover and rollback

The cutover job — the only job that moves traffic — runs under `environment: production` with Required reviewers. Inside the gated job, in order:

1. **snapshot** the owner's live DB (`switch.sh --snapshot`, a consistent `.backup`, streamed to the CI runner) — the owner keeps serving;
2. **restore** it onto the standby (`switch.sh --restore`, from stdin), with the standby app **stopped**;
3. **re-run the migrations + checks** on the standby (the runner, still stopped — the DB is quiescent);
4. **start** the standby app;
5. **flip** the forwarding-rule target (`gcloud compute forwarding-rules set-target houses-l4 …` with the existing `GOOGLE_SA_KEY` — the box never flips routes, so it needs no GCP credentials and has none);
6. **verify** public health on the new owner, continuously for the **settle window (5 minutes)** with `last_write` advancing; only then is the flip complete.

**Standby app lifecycle (explicit):** install-smoke: stopped → start → health/smoke → stop. Waiting: stopped. Cutover: stopped → [restore, recheck] → start → set-target → verify/settle (running). No step writes the standby DB while the runner operates on it; on any smoke failure the standby is already stopped and stays stopped.

**Role cycle (post-flip):** after settle, the previous owner idles as the next rollout's standby. The next rollout rebuilds it from the artifact at its own start (never at a cutover); the workflow's rebuild guard refuses to touch whichever resource the rule currently targets. Nothing is "discarded" at flip time.

**Rollback** is the same command in reverse: `set-target` back to the previous owner. No DB restore — the owner's database was never written by the rollout. The per-box flip, ACTIVE/PREVIOUS markers, and the pre-flip snapshot machinery are removed in Phase 2.

## The human approval gate for the smoke test (required, unchanged)

1. Install runs on the standby: artifact fetch, migrations-with-checks against the standby DB copy, health, `last_write` freshness, the existing property check — verdict lines in the run log.
2. **The cutover must not start until a human has seen that evidence and approved.** That is what the production-environment approval gates.
3. On any smoke failure — including any migration check — no approval is possible: the chain aborts, the standby stays stopped, the owner untouched.
4. Nothing in the phases removes or automates this checkpoint.

## Phases

1. **Migration pipeline:** the runner + manifest/check contract (incl. check CLI, backup naming, verdict format above) + tests; one-runner-call callers; delete `run-migration.sh` and the `--publish` mechanism (allowlist/workflow sites); bootstrap writes the `/opt/houses/SEED` provenance file; `--diagnose` prints it.
2. **Terraform + the two-instance data plane:**
   - `terraform/` over both instances, target instances, `houses-l4`, the address (with `prevent_destroy`), firewall, `houses-tfstate`, public-key-only metadata, instance SAs (drop `SEED_SA_KEY`), ephemeral SSH IPs.
   - **Adoption run** (one-off, gated like a cutover; accepted downtime = the seconds the address is unattached): detach `houses-static` from the owner's access config → `terraform apply` (the rule claims the address, targets the current owner) → verify. The owner's SSH (ephemeral IP) is unaffected throughout.
   - **The per-box layout collapses:** each box = one checkout (`/opt/houses/app`), one unit (`houses.service`, port 8765), Caddy on the standby serves nothing external (its smoke is local). The `houses-blue`/`houses-green` units, role ports 8765/8766, `ACTIVE`/`PREVIOUS`, the smoke hostname, and `run-instance.sh` port derivation are deleted; the rule target is the role marker. `switch.sh` keeps `--snapshot`/`--restore`/`--diagnose`; the per-box flip is gone (CI's `set-target` is the flip).
   - **Rebuild guard:** the workflow reads the rule target before any instance replacement and refuses to rebuild the targeted instance.
3. **Artifacts:** `build-artifact` job → `gs://houses-artifacts/<sha>.tar.gz`; `install-artifact.sh <object>`; bootstrap fetch by `PROVISION_ARTIFACT`; the `ARTIFACT` marker.

## Operations & troubleshooting

- **Humans: the operator key is full admin, unchanged** (locally `~/.ssh/houses_operator`): shell + passwordless sudo on either instance. The troubleshooting path; the allowlist constrains automation, not humans.
- **CI: least privilege.** The deploy key runs exactly the allowlist shapes — `install-artifact.sh <object>`, `switch.sh --snapshot`/`--restore`, `--diagnose`, journalctl. New shapes = explicit, reviewed allowlist + sudoers changes.
- **Evidence over state:** runner verdicts (the exact format above), transcripts, `/opt/houses/ARTIFACT`, `/opt/houses/SEED`, and release logs are the same artifacts humans and automation read.
- **Reproducibility:** the installed artifact sha answers "what is this box"; the SEED file answers "what did it start from".
- **Declared infra:** `terraform show`/`plan` = what should exist; drift is a finding, not a mystery.
- **Escape hatches:** serial console + startup-script logs when SSH is unreachable; gcloud for rule target and instance status.
- **Recovery, by layer:** rehearsal fails → release aborts, standby stays stopped. Flip fails → `set-target` back (the owner never changed). Standby broken → rebuild it from the artifact at the next rollout. Owner broken → rebuild it as a standby and `set-target` back, or re-provision from the trusted seed.

## Acceptance criteria

1. A rollout: build artifact → install on standby → migrations-with-checks → smoke → **human approval** → snapshot/restore/recheck → start → `set-target` → settle. Exactly one human decision.
2. A failed rehearsal = release aborts, production untouched. A failed flip = `set-target` back in one command, no DB restore.
3. The migration pipeline cannot report success without every check having run and passed — enforced by the parser and verdict gate (exact formats above), proven by fixture tests.
4. No destructive step precedes the trusted state: the address is `prevent_destroy`; the workflow refuses to rebuild the rule's target; the seed is human-validated; the owner's data is never migrated on.
5. Every rollout exercises the recovery path (the standby = the DR drill).

## Risks / tradeoffs

- Terraform state: an operator-created `houses-tfstate` bucket, CI SA scoped to it.
- The cutover window (snapshot → restore → recheck → set-target) and the one-time adoption detach are minutes of downtime by design.
- Paired-check quality is the load-bearing risk: a weak check is the new silent-skip. Mitigated by the verdict gate, fixture tests, and the human reviewer seeing verdicts.
- SA permission changes affect both boxes; they go through the release process, not ad-hoc.

## Sources

- Google SRE Workbook, ch. 16 *Canarying Releases* — https://sre.google/workbook/canarying-releases/
- Martin Fowler, *Blue Green Deployment* — https://martinfowler.com/bliki/BlueGreenDeployment.html
- HashiCorp, *What is Terraform* — https://developer.hashicorp.com/terraform/intro
- Considered and deliberately not adopted: Liquibase changelog/checksum model — https://docs.liquibase.com/concepts/changeset.html ; Fowler / Danilo Sato, *Parallel Change* — https://martinfowler.com/bliki/ParallelChange.html