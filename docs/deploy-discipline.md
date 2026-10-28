# Deploy discipline

How production changes happen — and the rules that keep them safe. The generic
principle lives in `skill://prod-deploy-via-release-only` (loaded when a task
touches a production system); this page is the houses-specific shape of it. The
design rationale is in
[docs/anti-fragile-rollout-plan.md](anti-fragile-rollout-plan.md); the operator
walkthrough is [tools/deploy/provision.md](../tools/deploy/provision.md).

## Production changes go ONLY through the rollout process

- The GitHub **Release** workflow. Eleven dispatch actions: `release` (build the
  artifact + install it on the standby), `prepare` (freeze the owner, snapshot
  it, restore+migrate that snapshot onto the standby, publish the review surface
  — then STOP), `flip` (the gated traffic move onto the reviewed box),
  `rollback` (the ungated undo), `provision` (rebuild a box from the artifact).
  Never SSH in and pull/restart the app directly; never run ad-hoc commands
  against prod to "just fix it".
- If the process is too slow or awkward, **improve it** — never bypass it. A
  skipped smoke gate or an unreviewed direct deploy is exactly the failure mode
  the process exists to prevent.
- Releases tag **main** only. Merge the PR first, then
  `git tag vX.Y.Z && git push origin vX.Y.Z`. A non-main ref is released only via
  an explicit `workflow_dispatch` with the `ref` input named — the deliberate,
  reviewable bypass. The workflow enforces this: a tag push not reachable from
  `main` fails the build job.

## One rollout, one human decision — the flip

```
build artifact → REBUILD the standby from it (fresh instance, bootstrap: seed
  + migrations) → install + rehearse + smoke        ── a BOOT proof, ungated
→ prepare (UNGATED: dispatching it is the decision):
    FREEZE the owner + snapshot it → restore + verify + MIGRATE that snapshot
    onto the standby → start it → publish the review surface   ── the real data
→ REVIEW https://houses-smoke.blueumbrella.net      ── the box the flip makes live
→ APPROVAL: "go live"
→ flip the L4 rules → retire the relay → settle (5 min of public health)
```

Every rollout builds a fresh box: no box drifts, and the bootstrap path is
exercised on every rollout. **`flip` is the human decision**, held by its
`production` environment gate and by `-f approval=approved` on the dispatch — two
locks, both required. `prepare` carries no gate: it moves no traffic, and a
second click on top of the dispatch it already required bought nothing. It does
take production down for the freeze, and says so on the run. Rollback needs no
approval.

**The split is the invariant: the box you review is the box that goes live.**
`prepare` freezes, snapshots, migrates and then STOPS, publishing the review surface; `flip` moves
traffic and touches nothing on that box. `recover`/`flip` have worked this way
since 2026-10-03 for exactly this reason — a job that restores or migrates
*after* its approval gate makes the live box different from the approved one.
`prepare` used to do that (snapshot, rebase and a second, unrehearsed migration
run after its gate); it now ends at REVIEW READY.

Two different databases appear above, deliberately. The fresh box's data at
install time is the **seed + migrations** — a human-validated bootstrap copy that
proves the box builds and boots, and nothing more. The data that will actually
serve is a **fresh snapshot of the frozen owner**, restored and migrated onto the
standby *before* the review. Snapshotting earlier than the freeze would serve
data that is minutes old and silently drop every write in between; snapshotting
later than the review would make the reviewed box not the live one. The freeze
is therefore the prepare step's first act and the review its last.

Stated plainly: **production is down from the freeze until the flip completes** —
the review window is downtime. That is the price of reviewing the real data on
the real box. The abort path is explicit: if anything after the freeze fails, CI
restarts the owner's app (`switch.sh --unfreeze`) and traffic never moved. A box
never unfreezes production on its own.

**The settle gate** is `flip`'s last step: 5 minutes of continuous public
`/health` polling on the new owner. It hard-fails on a missing site or on
`status`/`db` != ok; `last_write` staleness is reported, not gated, because a
converged DAG legitimately writes nothing for minutes. `/health` proves the box
is SERVING; it is not evidence about the data — that verdict is the runner's
`migrations: N applied+checked, 0 failed`, which the box refuses to start without.

**When the owner's data must not be carried forward** (a silent migration skip, a
stalled cascade, a botched restore), recovery is prep-first, approve-last:
`action=recover` is ungated prep and it leaves the box **FINAL** — the current
artifact installed (app + tooling), the restored database, the production role
URL, the app started, the review surface verified — and the
gated `action=flip` then only retires the smoke relay on the abandoned owner
and moves the traffic rules. Nothing on the approved box changes after the
approval, by construction (2026-10-02: recover used to restore after its gate;
2026-10-03: the flip used to install tooling and rewrite the role URL after
it — both meant the box that went live was never exactly the box approved).
The owner is never read; the writes since the chosen object are gone by
design. See tools/deploy/provision.md §7b.

**The rule's target is the only "who is live" fact.** The two instances
(`houses`, `houses-standby`) are fixed resources whose roles rotate with it;
Terraform ignores changes to the rules' target, so only the workflow's
`set-target` moves traffic. The static IP belongs to the forwarding rules and is
never attached to an instance — an instance's address is ephemeral and exists for
SSH (the control plane) only.

## The box enforces this mechanically

The box's sudoers (`/etc/sudoers.d/houses-deploy`, written by
`tools/deploy/box-setup.sh` from the artifact) grants the deploy user ONLY:

- `/opt/houses/install-artifact.sh *`
- `/opt/houses/switch.sh *`
- `/usr/bin/journalctl *` (read-only diagnostics)

...and the deploy key's `authorized_keys` entry forces every command through
`/opt/houses/deploy-allowlist.sh`, which rebuilds the argv from validated fields
and forwards only what the workflow calls: `install-artifact.sh <object>`,
`switch.sh --snapshot`, `switch.sh --unfreeze`, `switch.sh --rebase <rows>`,
`switch.sh --restore <gs://…db>`, `switch.sh --diagnose`. Everything else —
including `journalctl` — is a silent no-op. No interactive login — including the
maintenance SSH key and any agent session — can restart app units or mutate the
deployment. A direct deploy is impossible, not merely discouraged. Verify the
guard after any box rebuild: `sudo -n -l` must list exactly those three commands,
and `sudo -n systemctl restart houses` must fail.


## The human prod gate — the dispatch and the environment (double lock)

`flip` — the traffic move — is held by TWO independent locks; with either one
missing the job refuses:

1. **The GitHub environment gate.** Repo → Settings → Environments →
   **production** → Protection rules: **Required reviewers** = you,
   "Allow administrators to bypass" = false. Verify this is actually enforced:
   on 2026-10-02 the environment had NO protection rules (`protection_rules: []`),
   so GitHub autopassed the environment every run declared and a flip executed
   without a single approval.
2. **The dispatch's approval input.** `flip` and `recover`'s flip start with a
   "Human approval gate" step that fails loudly unless the dispatch carried
   `-f approval=approved` — before any box command, before any traffic change.
   The input defaults to `pending` (fail-closed); the environment gate is a
   second lock, never the only one.

`prepare` and `recover` (the prep halves) are ungated by design: they move no
traffic, and the dispatch itself is the human's act.

`rollback` stays ungated: it is the emergency undo. A traffic move without BOTH
locks is the 2026-10-02 incident; patch the workflow or this doc for a change
to what "approved" means, never the other way round.

## A UI feature is NOT done on green tests

Done means: (a) the persona walk of the live surface passes — tap the buttons,
observe outcomes, at the persona's device size (P13–P17,
`docs/ux-standards.md`) — and (b) every link of the feature's runtime chain has
been exercised against a live instance. A broken verification environment is a
blocker to fix, never a waiver.

## Diagnose failures from evidence

When the user reports a failure, inspect logs, queues, and service state before
responding. Never explain a failure with an unverified assumption (the answer is
usually one `journalctl` or status check away).

## Diagnosing a failed or stalled rollout

The box keeps the evidence even when the GitHub ssh dies mid-step (2026-09-07: a
deploy hung inside the live-DB snapshot for the full 90-minute CI budget and its
output died with the ssh). Every step of `install-artifact.sh` and `switch.sh`
mirrors to:

- `/opt/houses/logs/releases/*.log` — the full transcript, one file per run
  (`install-artifact-<ts>.log`, `switch-<ts>-<action>.log`).
- journald with tag `houses-rollout` — `journalctl -t houses-rollout`.

Start with `gh workflow run Release -f action=diagnose` (both boxes, read-only).
Key reads:

| What happened | Where to look |
|---|---|
| `FAILED: sha256 mismatch` | the object key is not the file's hash — the upload was truncated or swapped; rebuild the artifact |
| `FAILED: … cannot import houses/dag` | the packed venv is not relocatable — a build bug, not a box bug |
| `check FAILED:` in the runner's verdicts | a migration's paired check disagrees with the apply — the data is NOT migrated; read the check's own line |
| `FAILED: only N MiB free` | the box is starved; nothing was started (2026-09-07's OOM) |
| `FAILED: the app did not become healthy` | `journalctl -u houses.service --since="5 minutes ago"` (the script prints its tail too) |
| `FAILED: restored N rows, the snapshot captured M` | the transfer lost data; the standby rejected it and the owner is untouched |
| the two L4 rules disagree | a half-finished flip — `resolve` refuses to run rather than guess |
| `/health` body | `{status, db, last_write}` — a stalled database shows `db:"error"` or a stale `last_write`, never a bare "ok" |

Marker files on a box: `ARTIFACT` (sha256 + ref + when it was unpacked), `SEED`
(object + etag + rows + when it was restored), `PROVISIONED` (the bootstrap
finished). They answer "what is this box running" and "what did it start from" —
in files, not in anybody's memory.

## Why the DB copy used to hang — and the contract now

`sqlite3 .backup` in the CLI runs the sqlite backup API but its own retry loop is
`while rc==BUSY||LOCKED: sleep(250ms)` — **the `.timeout` busy handler does NOT
govern `.backup`**. One persistently-busy source means an unbounded wait; on
2026-09-07 that was a 90-minute silent hang inside the live-DB snapshot. WAL makes
it worse in a subtle way: a backup of a WAL database only sees what a checkpoint
has applied — a `:mode=ro` connection cannot checkpoint, so the copy silently
returns a stale/empty database (verified empirically: 0 rows). The snapshot
(`switch.sh --snapshot`) applies the safe pattern:

1. **Write-capable connection** (a read-only one cannot checkpoint the WAL; the
   checkpoint only normalizes WAL → main, it changes no data).
2. `PRAGMA wal_checkpoint(PASSIVE)` — never blocks a writer — so the copy
   includes the WAL's content.
3. The backup API with `pages=1000` and a **hard deadline** enforced both by the
   progress callback AND after the copy (a small copy can complete past the
   callback), so a cutover can NEVER wait unbounded on the live DB.
4. The copy is sanity-checked (`node_results` must contain rows) and its row
   count is printed for the restore to be verified against.

## A rollout must never OOM the box (2026-09-07 incident)

### What happened (evidence)

- 2026-09-05 16:13 switch to `efce41a`: healthy (public `/health` 200); box CPU
  idle after the first hour. The roll-forward itself did NOT break the box.
- 2026-09-07 07:34 v1.2.0 deploy: the box's *stale* `/opt/houses/release.sh` hung
  90 minutes at the live-DB snapshot (the CLI `.backup` loop above); CI killed the
  ssh at ~09:04, the standby was left running.
- 20:01 the e2-micro (953 MiB) hit **global OOM**: `cron invoked oom-killer …
  task_memcg=/system.slice/houses-green.service, task=node` →
  `houses-green.service: Failed with result 'oom-kill'`. The standby had
  crash-looped on a smoke DB overwritten mid-hang; every `Restart=always` cycle
  re-ran `npm install` + the Vite build on the box — node at 1.31 GB total-vm /
  350 MB anon.
- 2026-09-08 23:45+ the guest's network died (`OSConfigAgent: … metadata server …
  network is unreachable`, every 60 s). Nothing self-healed: prod unreachable for
  4 days while the VM reported RUNNING. The 2026-09-12 GCP stop/start restored
  networking; blue booted and served again.

### Root causes (all in the process, none in the application code)

1. A release started a **second full stack** (uvicorn + npm install + Vite build +
   static serve) beside the live one on a 953 MiB box.
2. The standby **persisted warm between releases** by design, and a failed release
   left it running; `Restart=always` with no `MemoryMax` and no cleanup turned one
   hung run into a 13-hour rebuild loop.
3. **No memory containment**: a global OOM can reap anything — including the
   guest's networking — and nothing reboots a limping guest.
4. The deploy job ran the **box's own copy** of the tooling, which was NOT
   versioned with the repo — the Sep-7 run executed the old, unbounded snapshot
   path even though main had already fixed it.

### The rules (still enforced; implementations updated 2026-09-25)

**R1 — Build everything off the box, and bake the OS.** CI builds the frontend
and the relocatable venv into the artifact; the OS layer (packages + Caddy) is
baked into the `houses-base` machine image, so a rollout does not depend on the
apt mirrors. The box never runs npm, Vite, or `uv sync`. The boot path
is `app/.venv/bin/python serve_prod.py` with the shipped `frontend/dist`. Kills
the node memory monster (the OOM victim) and makes the box's runtime identical to
what CI verified.

**R2 — The app is stopped except when serving.** `install-artifact.sh` stops the
app before it unpacks and stops it again after the smoke; `switch.sh --rebase`
starts it only once the restored database is verified and migrated. Between
rollouts the standby's app is stopped, and the box itself is replaced at the next
rollout's start — no second stack, ever.
**R3 — Contain memory per unit.** The systemd unit gets `MemoryMax=512M`,
`MemoryHigh=384M`, `OOMScoreAdjust=-800`, `Restart=on-failure` and a
`StartLimitIntervalSec/Burst` cap. An overrun kills ONE unit, never the guest; a
crash-loop stops instead of rebuilding forever.

**R4 — Pre-flight gates + envelopes.** `install-artifact.sh` refuses to start the
app below 450 MiB free; the workflow's step timeouts and the scripts' own
deadlines (300 s snapshot, 90-minute install, 90-minute rebuild-and-bootstrap —
540 polls, because a fresh box restores the seed and then migrates it: 243k
rows measured ~42 min) bound every run; the deploy key's `command=` restriction
ride inside the remote command itself.

**R5 — Self-heal the guest.** `houses-network-watchdog.timer` runs every 2 min;
`network-watchdog.sh` retries the GCP metadata server 3× and then
`systemctl reboot`s. Worst case is a ~6-minute downtime instead of a 4-day silent
outage. Enabled only on GCP guests (dmi product_name check).

**R6 — Tooling ships INSIDE the artifact (the only elevated path).** The deploy
key is `command=`-restricted in `authorized_keys` — no scp, no arbitrary sudo. The
artifact carries `tools/deploy/*` and `units/*`; `install-artifact.sh` unpacks it
and runs `box-setup.sh`, which installs the tooling root-owned and reloads
systemd. There is no separately-versioned box copy of anything: what a box runs is
the artifact it was installed from (`/opt/houses/ARTIFACT` says which),
`/opt/houses/SEED` says what it bootstrapped from, and `/opt/houses/RESTORED`
says which database it was last restored to. TLS is not the box's business any
more: Cloudflare terminates for clients and the box serves a Cloudflare origin
certificate fetched from the bucket (no ACME, no certificate carried between
boxes).

### The rollout order

1. Land the change in the repo (with tests) and merge to `main`.
2. `Release` with `action=release` → build the artifact, **rebuild the standby
   instance** from it, install, rehearse and smoke; read the transcript (the new
   box's seed → migrations → receipt, then the install's verify → receipt →
   migrations → smoke).
3. `Release` with `action=prepare` → **no gate; dispatching is the decision**.
   It freezes the owner, snapshots it, restores/migrates that snapshot onto the
   standby, starts it, and publishes the review surface — then stops. Production
   is down from the freeze until the flip.
4. **Review `https://houses-smoke.blueumbrella.net`** — the real data, the
   migrations already run on it, the exact box the flip will make live.
5. `Release` with `action=flip -f approval=approved` → preflight, move the L4
   rules, retire the relay, hold the 5-minute settle.
6. Confirm `https://houses.blueumbrella.net/health` and
   `terraform output live_target`.
7. Record the outcome (and anything surprising) in this file or the plan.

### Outcome — 2026-10-27, the first run of the split

`v1.5.4` (artifact `7a8d071f…`). `prepare` succeeded in ~12 minutes: snapshot
`gs://houses-artifacts/snapshots/20261026T194939Z.db`, restore verified at
**261,526 rows** (the owner's data — the seed is 243,004), `migrations: 2
applied+checked, 0 failed` (`split_node_results.py` a no-write no-op because
production was already split; `backfill_person_ids.py` applied with a backup),
app started, review surface published. `flip` passed the preflight, moved the
rules, retired the relay, and held **20 healthy reads over 5 minutes** of settle.

`houses` is now production; `houses-standby` is the abandoned frozen owner with
its database kept as-is.

**Production was down from the 19:49 freeze to the 11:01 flip — about 15 hours**,
because the review window is downtime by design. The migration took 12 minutes,
so almost all of it was the wait for review. That is the honest cost of
reviewing the real data on the real box, and a reason to keep that window
deliberately short.

Two things the run taught, both already fixed:

- **A renamed action can silently skip its job.** `prepare` reported success
  having done nothing: `image-stale`'s condition still tested `cutover`, so it
  skipped, `prepare`'s `needs.image-stale.result` was `skipped` rather than
  `success`, and `prepare` skipped too. Production never froze; the only signal
  was one word in the job list. There is now a guard that every action the
  dispatch offers is named by at least one job's `if`.
- **`last_write` stays stale through a flip**, correctly: the box was frozen and
  a converged DAG writes nothing. The settle reports it and does not gate on it.

### Outcome — 2026-10-28, the seed deleted and the image made load-bearing

`v1.5.9` (artifact `a8b2e41b…`). `prepare` snapshot **272,277 rows**
(`gs://houses-artifacts/snapshots/20261027T203834Z.db`), restore verified
`integrity=ok`, `migrations: 2 applied+checked, 0 failed`, then for the first
time the **data smoke ran against the rows that would ship**: `46 properties,
detail rendered, frontend served`. `flip` moved both L4 rules to
`houses-standby-target` and held **20 healthy reads over 5 minutes**.

Serving box: `/opt/houses/ARTIFACT ref=v1.5.9`,
`RESTORED source=live-snapshot rows=272277`. `houses` is now the abandoned
frozen owner.

**The rollout surface was smaller than the pipeline behind it.** Six defects
surfaced, each one visible only after the previous was fixed; the first four are
process defects, and two of them were mine:

| Symptom | Cause | Fix |
|---|---|---|
| `provision` took 537s, then 897s of it "waiting for bootstrap" | a 1.4 GB **seed** restored onto an e2-micro, then overwritten by `prepare` minutes later | seed deleted; a box starts with **no data** |
| stage `1. packages` cost **120.7s** on a baked image | the bootstrap apt-installed what the image was supposed to carry, so a stale image was invisible | bake **verifies** its layer before powering off; bootstrap only verifies, and its failure names `action=bake` and forbids installing |
| release failed "image source drifted" while the image matched main | `image-stale` checked out the **released ref** and called it main | gate always checks out `main` (one image serves every box) |
| `Error acquiring the state lock … 412` | a merge-triggered `dispatch-bake` and a manual `action=bake` overlapped | `provision`/`bake` share `concurrency: houses-terraform` |
| every push to main failed with **0 jobs** | an edit dropped `bake`'s `runs-on`; GitHub only says "workflow file issue" | actionlint is a CI step (it names the line) |
| `prepare` died after a good restore: `ENV_FILE: unbound variable` | the new smoke helper used `install-artifact.sh`'s variables, which `switch.sh` sets only in later branches | the helper declares its own locals |

**The lesson worth keeping:** when a rollout is slow or fails on a *box* step,
ask what the box was built with before patching the step. Three of the six were
an artifact or an image being wrong, and the tempting "fix" — install the
package here, keep the seed, copy the variables across — is what made the next
rollout worse. The bootstrap now refuses rather than repairs, and says which.

**Production was down from the 20:38 freeze to the 10:09 flip** on the date
boundary, again mostly review. `prepare` itself was ~20 minutes end to end
(1.4 GB snapshot stream, restore, two migrations).

## Data migrations ride the rollout (generic)

A data migration is a ref-shipped script executed by the ONE runner
`tools/deploy/run_migrations.py`, in TWO phases driven by the ONE shared manifest
`tools/deploy/migrations.list` (shipped to `/opt/houses/migrations.list`, and
inside the artifact):

1. **Install rehearsal** — `install-artifact.sh` runs the runner once against the
   STANDBY's own database before the smoke; a failed rehearsal aborts the install
   with the app stopped and prod untouched.
2. **Cutover rebase** — `switch.sh --rebase` restores the owner's snapshot onto
   the standby, verifies it (integrity check + row count), runs the runner once
   against that quiescent copy, and only then starts the app. A failure leaves the
   standby's app stopped and the owner serving, untouched. Rollback needs no
   restore: the owner's database was never written.

Both callers assert the runner's summary line, so a run that did not apply AND
check every migration can never be followed by a boot, a flip, or a "ready"
verdict.

**Manifest format** — one migration per line, an APPLY script and its CHECK
script, both relative to the checkout root:

```
scripts/backfill_person_ids.py scripts/backfill_person_ids.check.py
```

The parser (not a shell loop) rejects a malformed line, an absolute or escaping
path, a path listed twice, two apply scripts sharing a basename (the
pre-migration backup name is keyed on it), and a manifest with zero migrations —
`0 applied+checked, 0 failed` must never be the verdict of a run that did
nothing. The 2026-09-24 silent skip was a `while read` loop that dropped a final
line carrying no trailing newline; the parser reads it, and the
missing-migration failure can no longer masquerade as success.

**Verdicts** (the callers, `switch.sh --diagnose` and the human gate read exactly
this):
```
== run_migrations <iso-ts> manifest=<path> db=<path> mode=apply
migration <apply-basename>: apply ok; backup ok; check ok
migrations: <N> applied+checked, <M> failed
```
Exit 0 only when `<M>` is 0. Each run leaves one `run-migrations-<timestamp>.log`
(root-owned, newest 32 kept).

**Apply-script contract** (reference: `scripts/backfill_person_ids.py`):

- Dry-run by default with NO arguments (read-only; prints what the apply would
  do). `--apply` writes (honours `HOUSES_SCRIPTS_MAY_WRITE=1`);
  `--backup --backup-path <p>` writes the pre-migration copy (the runner passes
  `<db>.pre-<apply-basename>`); `--verify` re-scans after apply and must find
  nothing left to do.
- Idempotent: a re-install or a re-rebase finds zero work.
- Batched commits: a single-transaction run needed a ~2.4 GB rollback journal and
  crashed the box out-of-disk (2026-09-18). The runner refuses to start below DB
  size + 1 GiB free (`HOUSES_MIGRATION_MIN_FREE_BYTES` overrides it).

**Check-script contract** (reference: `scripts/backfill_person_ids.check.py`):
`<python> <check> --db <db>` opens the DB READ-ONLY, exits 0 iff the migration's
effect is complete, and never writes. It is a separate program from the apply, so
it cannot vouch for the code that just wrote — a check that merely repeated the
apply's claims would be worth nothing. Adding a migration = shipping BOTH scripts
+ one line in migrations.list.

**Revert layers** (all independent, all verified): the migration's own
pre-migration backup beside the database it ran on, the owner's snapshot kept by
the cutover step, and — for a box that was rebuilt — the seed plus the migrations
that carry it forward. **Rollback does not restore data**: it flips traffic back
to the box that never had its database written.

**Verification before trusting a backup**: `PRAGMA integrity_check` on the copy;
row counts equal between the snapshot and the restore; the persons row present;
`sha256sum` + size recorded in the transcript. Prove a restore works (the rebase
does exactly that, against the recorded row count) before it is ever needed.

## Log retention

`/opt/houses/logs/releases/` keeps the newest 32 runs of each of the
`install-artifact-*`, `switch-*` and `run-migrations-*` transcripts; older files
are deleted by the scripts themselves. journald's own size cap applies to the
`houses-rollout` tag (systemd default: 10% of the volume / 4 weeks — raise
`SystemMaxUse` in `/etc/systemd/journald.conf.d/` if a box needs more history).
