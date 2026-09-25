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
| Provision's idempotent `delete` destroyed the **live** box before the new one was ready | the launch step deletes `houses-rebuild` unconditionally; the live box had that name | Phase 2: the traffic owner is `houses`, `prevent_destroy`, never rebuilt by a rollout |
| Cutover's static-IP move failed (`At most one access config currently supported`) | the fresh instance keeps an auto-assigned ephemeral config; GCP allows one | Phase 2: the static IP never touches an instance; it is the forwarding rule's address, and the flip changes the rule's target |
| Fresh box's flip aborted (no `blue/.venv`) | the release only installs the standby side's venv | Phase 3: boxes install one artifact (venv included) fetched from GCS |
| **The migration silently never ran** | `migrations.list` shipped with no trailing newline; `while IFS= read -r MIG` skips a newline-less final line — the list was comments-only to the loop, so the flip reported success with nothing executed | Phase 1: a real parser + mandatory apply-then-check verdicts |

The migration skip is the emblematic failure: a safety-critical mechanism was disabled by **one missing byte** and nothing verified the run. The response is a runner that gets it right in the first place.

## Architecture (one view)

```
                     static IP (houses-static, regional)
                                │  address of
                                ▼
                  L4 forwarding rule (houses-l4, ports 80/443)   ← data plane
                                │  target =
                                ▼
   ┌─────────────────────┐      ┌──────────────────────┐
   │ houses              │      │ houses-standby       │
   │ traffic owner       │◄─────┤ rolling / disposable │   control plane:
   │ prevent_destroy     │ nothing│ ephemeral ext IP = SSH  │
   │ Caddy terminates TLS│      │ Caddy (inactive)     │
   └─────────────────────┘      └──────────────────────┘
          server (SSH)                  │ server (SSH)
```

- **Data plane**: the static IP is `houses-l4`'s address, and nothing else. The rule's `target` points at the active instance's `google_compute_target_instance`. A rollout flips `target` — an IP is never attached to or detached from an instance again (the access-config class dies).
- **Control plane**: both instances keep auto-assigned ephemeral external IPs for SSH (deploy key + operator key). No IP juggling.
- **Credentials**: boxes hold no secrets. Each instance runs with its GCP service account (`houses-box-deploy`); gsutil/gcloud use the metadata server identity. No key file on the box, none in Terraform state. Metadata carries only public keys (`ssh-keys` pubkeys) and public vars (`PROVISION_REF`, `PROVISION_ARTIFACT`).
- **Migrations run on the standby's DB copy** — never on the traffic owner's data. Rehearsal at install time; a fresh snapshot + re-run immediately before the flip.
- **One human decision**: the cutover approval. Everything else is automated.

## Decisions (implementation-review questions, answered)

| # | Question | Answer |
|---|---|---|
| Q1 | Base directory for manifest-relative script paths? | The runner takes `--scripts-dir <abs side checkout>`; manifest paths resolve against it. Callers already know the side (`/opt/houses/green` at release, the new side at flip). The parser refuses resolved paths that don't exist. One manifest, explicit root, no hidden convention. |
| Q2 | Which interpreter runs apply/check? | The runner takes `--python <abs venv python>` (the side's `.venv/bin/python` — migrations import `houses.*`). The runner validates it is executable. The runner itself is stdlib-only because it only orchestrates subprocesses. |
| Q3 | Forwarding-rule shape? | `google_compute_forwarding_rule houses-l4`: `ip_address = houses-static`, `ports = [80, 443]`, `target = houses-target.id`. Two zonal `google_compute_target_instance` resources (one per instance); the flip = change the rule's `target`. Caddy stays on the box, TLS unchanged (L4). |
| Q4 | Recurring two-instance lifecycle + adoption flag? | Every rollout: rebuild `houses-standby` from the artifact, smoke it, gate, flip the rule, then (after settle) the old owner becomes the next standby — roles rotate via the rule, the names stay fixed. Adoption = a one-off `action: adopt` workflow input that runs import + applies the config WITHOUT the cutover job. |
| Q5 | How does the cutover's fresh snapshot reach the standby? | CI mediates, no new credentials: `ssh houses "sudo /opt/houses/switch.sh --snapshot"` streams the consistent `.backup` to the runner, `ssh houses-standby "sudo /opt/houses/switch.sh --restore"` consumes it (stdin), then the runner re-checks the standby. Transient cutover data, never the seed — distinct from the removed `--publish`. |
| Q6 | Who flips the route, with what? | CI, with the existing `GOOGLE_SA_KEY`: `gcloud compute forwarding-rules set-target houses-l4 --target …` in the gated cutover job. The box never flips routes (it has no GCP credentials and needs none). |
| Q7 | Phase-2 rollback shape + DB semantics? | The rollback action runs the same `set-target` back to the original instance. No DB restore: the traffic owner's DB was never touched (migrations ran on the standby's copy); the standby is discarded. Simpler than today's snapshot-restore. |
| Q8 | Provision/terraform invocation, secrets, external IP? | Terraform owns: both instances (with `access_config {}` = ephemeral SSH IPs), the address, the firewall, the target instances, the rule, and the off-box state in `houses-tfstate`. CI keeps the guard steps around apply. Metadata = pubkeys + `PROVISION_REF` + `PROVISION_ARTIFACT` only; **no SEED_SA_KEY anywhere** — the instance SA replaces it, so bootstrap reads GCS directly. Apply is automated in the provision job (like today's gcloud); the human gate stays solely on cutover. |
| Q9 | Who builds the bundle, how? | New `build-artifact` job: checkout ref → `uv sync` (pinned lock) → frontend `npm ci && npm run build` → one tarball: code + `frontend/dist` + `.venv` + units + tools + `migrations.list` + checks → `sha256` → `gsutil cp gs://houses-artifacts/<sha>.tar.gz` (CI SA). Same OS on runner and box (ubuntu-24.04) so the venv is portable. |
| Q10 | `install-artifact.sh` interface + deploy sequence? | One new sanctioned shape: `sudo /opt/houses/install-artifact.sh <object>` (object = `houses-artifacts/<sha>.tar.gz`, charset-validated). It fetches with the instance SA, verifies the sha256 equals the key, unpacks over the standby side, runs the runner (rehearsal) and the smoke, streams verdicts. The stdin-dist pipe and `release.sh <ref>` go away; tooling ships inside the artifact. |
| Q11 | Fresh-box bootstrap fetch? | Metadata carries `PROVISION_ARTIFACT` = the object key (public). Bootstrap step 2: `gsutil cp` it with the instance SA (available from first boot via the metadata server), unpack, done — no clone, no SEED_SA_KEY, no ordering dance. |
| Q12 | Which hash does ARTIFACT/--diagnose record? | The bundle's content sha256 (the object key) — "what is this box" = exact bytes. The git ref stays as provenance (`houses-revision` marker, second line). `--diagnose` prints both. |

## The runner

`tools/deploy/run_migrations.py` — stdlib, tested, the only manifest reader:

```
run_migrations.py --manifest <path> --db <path> --scripts-dir <abs side checkout> --python <abs venv python> [--apply | --dry-run]
```

- Manifest: `migrations.list`, one line per migration: `<apply-script> <check-script>` (paths resolve against `--scripts-dir`). The parser rejects malformed lines, unknown/duplicate paths, and a final line without a newline — the file-shape class cannot parse.
- `--apply`: space gate (DB + 1 GiB, `HOUSES_MIGRATION_MIN_FREE_BYTES`) → per migration: dry-run report → apply+backup (with `<db>.pre-<migration>`) → **independent check subprocess** (exit 0 iff the migration's effect is complete) → one verdict line. Non-zero exit on any failure. `--dry-run` = read-only report, same space-gate semantics.
- Ports every guarantee of the deleted `run-migration.sh` (space gate, backup, dry-run-first, post-apply chown/chmod); the script and its test file are removed.
- Transcript: `run-migrations-<timestamp>.log`, root-owned, pruned newest-32; `switch.sh --diagnose` tails it.
- Callers: `release.sh` (rehearsal, standby DB copy) and `switch.sh` (flip, live DB, prod stopped) each make **one** runner call; non-zero = abort; a flip failure restores the pre-flip snapshot and the old side.
- Tests: the real backfill + its `scripts/backfill_person_ids.check.py` against a seeded temp DB — apply ran, check ran, skipped/incomplete migration fails the run; parser failure cases; verdict lines present.

## The human approval gate for the smoke test (required, unchanged)

**The cutover — any traffic change — stays a human-gated step. The gate is the smoke evidence.**

1. `deploy-new`/install runs on the standby: artifact install, migrations-with-checks against the standby DB copy, health, `last_write` freshness, the existing property check — verdict lines in the run log.
2. The `cutover` job — the only job that moves traffic — runs under `environment: production` with Required reviewers. **The reviewer's approval is the smoke gate; the cutover must not start until a human has seen the evidence and approved.** Inside the gated job: fresh snapshot from the owner → restore on the standby → runner re-check → flip the forwarding-rule target → verify public health.
3. On any smoke failure — including any migration check — no approval is possible: the chain aborts, the standby stays stopped, the owner untouched.
4. Nothing removes or automates this checkpoint. Automation replaces everything around it; the decision to move traffic remains with a human looking at evidence.

## Phases

1. **Migration pipeline**: the runner + manifest/check contract + tests; one-runner-call callers; delete `run-migration.sh` and the `--publish` mechanism (allowlist/workflow sites); bootstrap records the seed object it restored.
2. **Terraform + the two-instance data plane**: `terraform/` with import-first adoption (`action: adopt`), `houses` + `houses-standby`, target instances + `houses-l4`, `prevent_destroy` on owner/address, instance SAs (drop SEED_SA_KEY), ephemeral SSH IPs; `switch.sh --snapshot`/`--restore`; cutover = snapshot → restore → runner → human-approved `set-target` → verify; rollback = `set-target` back.
3. **Artifact**: `build-artifact` job → `gs://houses-artifacts/<sha>.tar.gz`; `install-artifact.sh <object>`; bootstrap fetches `PROVISION_ARTIFACT`; `ARTIFACT` marker (sha + ref); `--diagnose` prints it.

## Operations & troubleshooting

- **Humans: the operator key is full admin, unchanged** (locally `~/.ssh/houses_operator`): shell + passwordless sudo on either instance. The troubleshooting path; the allowlist constrains automation, not humans.
- **CI: least privilege.** The deploy key runs exactly the allowlist shapes — `release.sh <ref>`-equivalents, `switch.sh --snapshot`/`--restore`, `install-artifact.sh <object>`, `--diagnose`, journalctl. New shapes = explicit, reviewed allowlist + sudoers changes.
- **Evidence over state:** runner verdicts + transcripts + release logs are the same artifacts humans and automation read.
- **Reproducibility:** the installed artifact sha answers "what is this box"; `--diagnose` prints it with the ref.
- **Declared infra:** `terraform show`/`plan` = what should exist; drift is a finding.
- **Escape hatches:** serial console + startup-script logs when SSH is unreachable; gcloud for rule target and instance status.
- **Recovery, by layer:** rehearsal fails → release aborts, standby stays stopped. Flip fails → `set-target` back (the owner never changed). Box broken → re-provision the standby from the artifact (Phase 2+); owner broken → rebuild it as a standby and flip back, or re-provision from the trusted seed.

## Acceptance criteria

1. A rollout: build artifact → install on standby → migrations-with-checks → smoke → **human approval** → snapshot/restore/recheck → `set-target` → settle. Exactly one human decision.
2. A failed rehearsal = release aborts, production untouched. A failed flip = `set-target` back in one command, no DB restore needed.
3. The migration pipeline cannot report success without every check having run and passed — enforced by the parser and verdict gate, proven by fixture tests.
4. No destructive step precedes the trusted state: `prevent_destroy` on the owner; the seed is human-validated (`seed-box.sh` only); the owner's data is never migrated on.
5. Every rollout exercises the recovery path (the standby = the DR drill).

## Risks / tradeoffs

- Terraform state: `houses-tfstate` bucket created once by the operator; CI SA scoped to it.
- The Phase-2 cutover window (snapshot → restore → runner → set-target) is minutes of downtime by design.
- Paired-check quality is the load-bearing risk: a weak check is the new silent-skip. Mitigated by the verdict gate, fixture tests, and the human reviewer seeing verdicts.
- Instance-attached SA permission changes affect both boxes — `houses-box-deploy` gets scoped read on `houses-artifacts` + `houses-seed`; changes go through the release process, not ad-hoc.

## Sources

- Google SRE Workbook, ch. 16 *Canarying Releases* — https://sre.google/workbook/canarying-releases/
- Martin Fowler, *Blue Green Deployment* — https://martinfowler.com/bliki/BlueGreenDeployment.html
- HashiCorp, *What is Terraform* — https://developer.hashicorp.com/terraform/intro
- Considered and deliberately not adopted: Liquibase changelog/checksum model — https://docs.liquibase.com/concepts/changeset.html ; Fowler / Danilo Sato, *Parallel Change* — https://martinfowler.com/bliki/ParallelChange.html