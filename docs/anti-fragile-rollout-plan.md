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
| Provision's idempotent `delete` destroyed the **live** box before the new one was ready | the launch step deletes `houses-rebuild` unconditionally; the live box had that name | the traffic owner is `houses`, `prevent_destroy`, never rebuilt by a rollout |
| Cutover's static-IP move failed (`At most one access config currently supported`) | the fresh instance keeps an auto-assigned ephemeral config; GCP allows one | the static IP never touches an instance; it is an L4 forwarding rule's address, and the flip changes the rule's target |
| Fresh box's flip aborted (no `blue/.venv`) | the release only installs the standby side's venv | boxes install one artifact (venv included) fetched from GCS by the instance's own identity |
| **The migration silently never ran** | `migrations.list` shipped with no trailing newline; `while IFS= read -r MIG` skips a newline-less final line — the list was comments-only to the loop, so the flip reported success with nothing executed | a real parser in a tested runner + mandatory apply-then-check verdicts |

## Architecture

```
                     static IP (houses-static, regional)
                                │  address of
                                ▼
                  L4 forwarding rule (houses-l4, ports 80/443)   ← data plane
                                │  target =
                                ▼
      ┌────────────────────┐    ┌──────────────────────┐
      │ houses             │    │ houses-standby       │
      │ traffic owner      │    │ rolling / disposable │   control plane:
      │ prevent_destroy    │    │ ephemeral ext IP =   │
      │ Caddy terminates   │    │ SSH only             │
      │ TLS on 80/443      │    │                      │
      └────────────────────┘    └──────────────────────┘
             ssH / verifies             ssH / runs migrations
```

Four invariants, each stated so it is obvious why the design cannot go wrong:

1. **The static IP belongs to the forwarding rule and nothing else.** Instance access configs are ephemeral and for SSH only (control plane); the data plane is `houses-l4` whose `target` is a zonal `google_compute_target_instance` for the active box. A rollout changes the rule's target — an IP is never attached to or detached from an instance, so the access-config failure class cannot occur. Caddy stays on the box (L4 passthrough, TLS unchanged).
2. **Boxes hold no secrets.** Each instance runs with its GCP service account (`houses-box-deploy`); every gsutil/gcloud call uses the metadata-server identity. Metadata carries only public keys (`ssh-keys` pubkeys) and public vars (`PROVISION_REF`, `PROVISION_ARTIFACT`). No key file exists on a box and none can appear in Terraform state.
3. **Migrations run only on the standby's DB copy.** Rehearsal at install; a fresh snapshot of the owner's DB is pulled and re-checked immediately before the flip. The owner's data is never migrated on, so a failed migration can never touch production data.
4. **Exactly one human decision: the cutover approval.** Everything else in the rollout is automated; the gate is the smoke evidence.

## The runner

`tools/deploy/run_migrations.py` — stdlib, tested, the only reader of the migration manifest:

```
run_migrations.py --manifest <path> --db <path> --scripts-dir <abs side checkout> --python <abs venv python> [--apply | --dry-run]
```

- **Paths and interpreter are explicit caller inputs.** The manifest holds relative `<apply-script> <check-script>` pairs resolved against `--scripts-dir`, so the same shipped manifest serves both checkouts (`houses` at flip, `houses-standby` at release) with no hidden convention about where scripts live; `--python` names the side's `.venv/bin/python`, which the migration scripts require (they import `houses.*`). The runner validates both resolve and exist. The runner itself is stdlib because it only orchestrates subprocesses.
- **Manifest format:** one line per migration, whitespace-separated paths; the parser rejects malformed lines, unknown or duplicate paths, and a final line without a newline — the file-shape failure class cannot parse.
- **`--apply`:** space gate (DB + 1 GiB, `HOUSES_MIGRATION_MIN_FREE_BYTES`) → per migration: dry-run report → apply + `<db>.pre-<migration>` backup → **independent check subprocess** (the migration's own `.check` script; exit 0 iff the effect is complete) → one verdict line → non-zero exit on any failure. The check is a separate process so it cannot vouch for the code that just wrote. `--dry-run` = read-only report with the same space-gate semantics.
- **Callers make exactly one runner call.** `release.sh` (rehearsal against the standby DB copy) and `switch.sh` (the flip, live DB, prod stopped) each invoke the runner once; non-zero = abort (release stops; flip restores the pre-flip snapshot and the old side). All of the deleted `run-migration.sh`'s guarantees — space gate, backup, dry-run-first, post-apply chown/chmod — live inside the runner.
- **Evidence:** one `run-migrations-<timestamp>.log` per run, root-owned, pruned newest-32; `switch.sh --diagnose` tails it. Verdict lines are the checkpoint values the human gate reviews.
- **Tests:** the real backfill plus its `scripts/backfill_person_ids.check.py` against a seeded temp DB — apply ran, check ran, a deliberately skipped or incomplete migration fails the run; parser failure cases; verdict lines present.

## Artifacts and bootstrap

- **The artifact is a content-addressed object in GCS** — `gs://houses-artifacts/<sha256>.tar.gz` — built by a `build-artifact` CI job: pinned `uv sync` + frontend `npm ci && npm run build` into one tarball (code, `frontend/dist`, `.venv`, units, tools, `migrations.list`, checks); the sha256 of the bundle is the object key, so identity and content are one value and re-building the same ref reproduces the same object. The choice of GCS over the GitHub artifact store is driven by who consumes it: the **box**, at first boot and at install, using its instance SA via the metadata server — no token, no secret, no ssh re-pipe (the size-capped stdin transport Phase 3 removes). GitHub run-scoped artifacts are for humans and CI, not for a box fetching by machine identity.
- **Install:** one new sanctioned shape, `sudo /opt/houses/install-artifact.sh <object>` (charset-validated). It fetches with the instance SA, verifies the sha256 equals the key, unpacks over the standby side, runs the runner (rehearsal) and the smoke, and streams verdicts. The stdin-dist pipe and the per-side git checkout disappear; tooling ships inside the artifact.
- **Fresh box:** `PROVISION_ARTIFACT` in metadata (public) names the object; bootstrap step 2 fetches and unpacks it with the instance SA — available from first boot via the metadata server, so there is no ordering dance and no `SEED_SA_KEY` anywhere. The artifact hash answers "what is this box": `/opt/houses/ARTIFACT` records `<sha256>` + `<git ref>` (ref = provenance, sha = identity); `--diagnose` prints both.

## Cutover and rollback

The cutover job — the only job that moves traffic — runs under `environment: production` with Required reviewers. Inside the gated job, in order: **snapshot the owner's live DB** (`switch.sh --snapshot` streams a consistent `.backup` to the CI runner), **restore it onto the standby** (`switch.sh --restore` consumes it), **re-run the migrations + checks on the standby**, then **flip the forwarding-rule target** (`gcloud compute forwarding-rules set-target houses-l4 …` with the existing `GOOGLE_SA_KEY` — the box never flips routes, so it needs no GCP credentials and has none), then **verify public health**. The snapshot transfer is CI-mediated transient cutover data — never the seed, which remains a human-validated artifact written only by `seed-box.sh`.

**Rollback is the same command in reverse**: `set-target` back to the original instance. There is no DB restore — the owner's database was never written by the rollout; the standby is discarded and rebuilt by the next rollout. The pre-flip snapshot machinery that exists today becomes vestigial and is removed.

## The human approval gate for the smoke test (required, unchanged)

1. Install runs on the standby: artifact fetch, migrations-with-checks against the standby DB copy, health, `last_write` freshness, the existing property check — verdict lines in the run log.
2. **The cutover must not start until a human has seen that evidence and approved.** That is what the production-environment approval gates.
3. On any smoke failure — including any migration check — no approval is possible: the chain aborts, the standby stays stopped, the owner untouched.
4. Nothing in the phases removes or automates this checkpoint.

## Phases

1. **Migration pipeline:** the runner + manifest/check contract + tests; one-runner-call callers; delete `run-migration.sh` and the `--publish` mechanism (allowlist/workflow sites); bootstrap records the seed object it restored.
2. **Terraform + the two-instance data plane:** `terraform/` over both instances (imported first, `prevent_destroy` on the owner and the address), target instances + `houses-l4`, public-key-only metadata, instance SAs (drop `SEED_SA_KEY`), ephemeral SSH IPs (`access_config {}`); `switch.sh --snapshot`/`--restore`; the gated cutover sequence above; rollback = `set-target` back.
3. **Artifacts:** `build-artifact` job → `gs://houses-artifacts/<sha>.tar.gz`; `install-artifact.sh <object>`; bootstrap fetch by `PROVISION_ARTIFACT`; the `ARTIFACT` marker.

## Operations & troubleshooting

- **Humans: the operator key is full admin, unchanged** (locally `~/.ssh/houses_operator`): shell + passwordless sudo on either instance. The troubleshooting path; the allowlist constrains automation, not humans.
- **CI: least privilege.** The deploy key runs exactly the allowlist shapes — `install-artifact.sh <object>`, `switch.sh --snapshot`/`--restore`, `--diagnose`, journalctl. New shapes = explicit, reviewed allowlist + sudoers changes.
- **Evidence over state:** runner verdicts, transcripts, and release logs are the same artifacts humans and automation read.
- **Reproducibility:** the installed artifact sha answers "what is this box"; `--diagnose` prints it with the ref.
- **Declared infra:** `terraform show`/`plan` = what should exist; drift is a finding, not a mystery.
- **Escape hatches:** serial console + startup-script logs when SSH is unreachable; gcloud for rule target and instance status.
- **Recovery, by layer:** rehearsal fails → release aborts, standby stays stopped. Flip fails → `set-target` back (the owner never changed). Standby broken → re-provision it from the artifact. Owner broken → rebuild it as a standby and flip back, or re-provision from the trusted seed.

## Acceptance criteria

1. A rollout: build artifact → install on standby → migrations-with-checks → smoke → **human approval** → snapshot/restore/recheck → `set-target` → settle. Exactly one human decision.
2. A failed rehearsal = release aborts, production untouched. A failed flip = `set-target` back in one command, no DB restore.
3. The migration pipeline cannot report success without every check having run and passed — enforced by the parser and verdict gate, proven by fixture tests.
4. No destructive step precedes the trusted state: `prevent_destroy` on the owner and address; the seed is human-validated; the owner's data is never migrated on.
5. Every rollout exercises the recovery path (the standby = the DR drill).

## Risks / tradeoffs

- Terraform state: an operator-created `houses-tfstate` bucket, CI SA scoped to it.
- The cutover window (snapshot → restore → runner → set-target) is minutes of downtime by design.
- Paired-check quality is the load-bearing risk: a weak check is the new silent-skip. Mitigated by the verdict gate, fixture tests, and the human reviewer seeing verdicts.
- SA permission changes affect both boxes; they go through the release process, not ad-hoc.

## Sources

- Google SRE Workbook, ch. 16 *Canarying Releases* — https://sre.google/workbook/canarying-releases/
- Martin Fowler, *Blue Green Deployment* — https://martinfowler.com/bliki/BlueGreenDeployment.html
- HashiCorp, *What is Terraform* — https://developer.hashicorp.com/terraform/intro
- Considered and deliberately not adopted: Liquibase changelog/checksum model — https://docs.liquibase.com/concepts/changeset.html ; Fowler / Danilo Sato, *Parallel Change* — https://martinfowler.com/bliki/ParallelChange.html