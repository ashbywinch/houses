# Deploy discipline

How production changes happen — and the rules that keep them safe. The
generic principle lives in `skill://prod-deploy-via-release-only` (loaded
when a task touches a production system); this page is the houses-specific
shape of it.

## Production changes go ONLY through the release process

- The GitHub **Release** workflow (tag `v*` → deploy to the standby →
  smoke → switch) or, on the box, `/opt/houses/release.sh` +
  `/opt/houses/switch.sh`. Never SSH in and pull/restart the app directly;
  never run ad-hoc commands against prod to "just fix it".
- If the release process is too slow or awkward, **improve it** — never
  bypass it. A skipped smoke gate or an unreviewed direct deploy is exactly
  the failure mode the process exists to prevent.
- Releases tag **main** only. Merge the PR first, then
  `git tag vX.Y.Z && git push origin vX.Y.Z`. A non-main ref is released
  only via an explicit `workflow_dispatch` with the `ref` input named —
  the deliberate, reviewable bypass. The workflow enforces this: a tag
  push not reachable from `main` fails the deploy job.

## The box enforces this mechanically

The box's sudoers (`/etc/sudoers.d/houses-deploy`, written by
`tools/deploy/box-setup.sh` on every fresh box) grants the deploy user
ONLY:

- `/opt/houses/release.sh *`
- `/opt/houses/switch.sh *`
- `/usr/bin/journalctl *` (read-only diagnostics)

No interactive login — including the maintenance SSH key and any agent
session — can restart app units or mutate the deployment. A direct deploy
is impossible, not merely discouraged. Verify the guard after any box
reprovision: `sudo -n -l` must list exactly those three commands, and
`sudo -n systemctl restart houses-blue` must fail.

## A UI feature is NOT done on green tests

Done means: (a) the persona walk of the live surface passes — tap the
buttons, observe outcomes, at the persona's device size (P13–P17,
`docs/ux-standards.md`) — and (b) every link of the
feature's runtime chain has been exercised against a live instance. A
broken verification environment is a blocker to fix, never a waiver.

## Diagnose failures from evidence

When the user reports a failure, inspect logs, queues, and service state
before responding. Never explain a failure with an unverified assumption
(the answer is usually one `journalctl` or status check away).

## Diagnosing a failed or stalled release

The box keeps the evidence even when the GitHub ssh dies mid-release
(2026-09-07: a deploy hung inside the live-DB snapshot for the full
90-minute CI budget and its output died with the ssh). Every step of
`release.sh` and `switch.sh` mirrors to:

- `/opt/houses/logs/releases/*.log` — the full transcript, one file per
  run (`release-<ts>-<ref>-<side>.log`, `switch-<ts>-<action>.log`).
- journald with tag `houses-release` — `journalctl -t houses-release`.

Key reads:

| What happened | Where to look |
|---|---|
| `release: snapshot attempt N failed or timed out` + a `live DB state:` line | writer contention on the live DB — the log carries the ACTIVE side's journald tail + `lsof` as evidence |
| ssh connect timeout in CI (exit 255) | the box is unreachable on 22 — the GitHub job now fails in ~20s, and the box-side log step reports `/opt/houses/logs/releases/` unreadable |
| standby not healthy after restart | `journalctl -u houses-<side> --since="5 minutes ago"`, plus the `-revision` marker (`/opt/houses/<side>-revision`) to confirm what got checked out |
| `/health` body | now `{status, db, last_write}` — a stalled database shows `db:"error"` or a stale `last_write`, never a bare "ok" |

Marker files on the box: `ACTIVE` (live side), `PREVIOUS` (last flip),
`SMOKE_READY` (standby smoke passed), `<side>-revision` (checked-out
commit). Pre-flip DB snapshots accumulate in `/var/backups/houses-pre-flip-*.db`.

## Why the DB copy used to hang — and the contract now

`sqlite3 .backup` in the CLI runs the sqlite backup API but its own retry
loop is `while rc==BUSY||LOCKED: sleep(250ms)` — **the `.timeout` busy
handler does NOT govern `.backup`**. One persistently-busy source means an
unbounded wait; on 2026-09-07 that was a 90-minute silent hang inside the
live-DB snapshot. WAL makes it worse in a subtle way: a backup of a WAL
database only sees what a checkpoint has applied — a `:mode=ro` connection
cannot checkpoint, so the copy silently returns a stale/empty database
(verified empirically: 0 rows). The release scripts now apply the safe
pattern:

1. **Write-capable connection** (a read-only one cannot checkpoint the WAL;
   the checkpoint only normalizes WAL -> main, it changes no data).
2. `PRAGMA wal_checkpoint(PASSIVE)` — never blocks a writer — so the copy
   includes the WAL's content.
3. The backup API with `pages=1000` and a **hard 120s deadline** enforced
   both by the progress callback AND after the copy (a small copy can
   complete past the callback), so the release can NEVER wait unbounded on
   the live DB.
4. The smoke copy is sanity-checked (`node_results` must contain rows) —
   a stale or empty standby refuses to pass.


## A release must never OOM the box (2026-09-07 incident)

### What happened (evidence)

- 2026-09-05 16:13 switch to `efce41a`: healthy (public `/health` 200);
  box CPU idle after the first hour. The roll-forward itself did NOT break
  the box.
- 2026-09-07 07:34 v1.2.0 deploy: the box's *stale* `/opt/houses/release.sh`
  hung 90 minutes at the live-DB snapshot (the CLI `.backup` loop above);
  CI killed the ssh at ~09:04, the standby was left running.
- 20:01 the e2-micro (953 MiB) hit **global OOM**: `cron invoked
  oom-killer … task_memcg=/system.slice/houses-green.service, task=node` →
  `houses-green.service: Failed with result 'oom-kill'`. The standby had
  crash-looped on a smoke DB overwritten mid-hang; every `Restart=always`
  cycle re-ran `npm install` + the Vite build on the box — node at
  1.31 GB total-vm / 350 MB anon.
- 2026-09-08 23:45+ the guest's network died (`OSConfigAgent: … metadata
  server … network is unreachable`, every 60 s). Nothing self-healed: prod
  unreachable for 4 days while the VM reported RUNNING. The 2026-09-12
  GCP stop/start restored networking; blue booted and served again.

### Root causes (all in the process, none in the application code)

1. A release starts a **second full stack** (uvicorn + npm install + Vite
   build + static serve) beside the live one on a 953 MiB box.
2. The standby **persists warm between releases** by design, and a failed
   release leaves it running; `Restart=always` with no `MemoryMax` and no
   cleanup turned one hung run into a 13-hour rebuild loop.
3. **No memory containment**: a global OOM can reap anything — including
   the guest's networking — and nothing reboots a limping guest.
4. The deploy job runs the **box's own copy** of `release.sh`
   (`sudo /opt/houses/release.sh $REF`), which is NOT versioned with the
   repo — the Sep-7 run executed the old, unbounded snapshot path even
   though main had already fixed it.

### Rules (implemented 2026-09-12)

**R1 — Build the frontend off the box.** CI runs `npm ci` + `npm run
build` and ships the tarball; `release.sh` unpacks it into the standby
(`HOUSES_DIST_TARBALL`), and `make run-prod`'s new `frontend-prod` target
skips npm entirely when a dist is already present (or
`HOUSES_SKIP_FRONTEND_BUILD=1`). npm never runs on the box — kills the
node memory monster (the OOM victim). Fallback to an on-box build remains
for bootstrap/manual bring-up.

**R2 — The standby is ephemeral.** `release.sh` installs a `trap cleanup
EXIT` that stops the standby at the end of a successful smoke AND on any
error — no second stack ever persists. The switch starts the new side
cold (`switch.sh` calls `systemctl restart` itself, which starts a stopped
unit). Note: `houses-smoke.blueumbrella.net` 502s between release-ready
and the switch — acceptable; the smoke already passed.

**R3 — Contain memory per unit.** The systemd units get `MemoryMax=512M`,
`MemoryHigh=384M`, `OOMScoreAdjust=-800`, `Restart=on-failure` and a
`StartLimitIntervalSec/Burst` cap. An overrun kills ONE unit, never the
guest; a crash-loop stops instead of rebuilding forever.

**R4 — Pre-flight gate + envelope.** `release.sh` refuses to start the
standby below 450 MiB free; the workflow's local `timeout 900 ssh` +
the step's 90-minute cap bound the run (the deploy key's `command=`
restriction means no wrapper may ride inside the remote command itself);
the snapshot carries its own 120 s deadline (PR 105).

**R5 — Self-heal the guest.** `houses-network-watchdog.timer` runs every
2 min; `network-watchdog.sh` retries the GCP metadata server 3× and then
`systemctl reboot`s. Worst case is a ~6-minute downtime instead of a
4-day silent outage. Enabled only on GCP guests (dmi product_name check).

**R6 — Tooling ships INSIDE the release (the only elevated path).** The
deploy key is `command=`-restricted in `authorized_keys` to
`sudo /opt/houses/release.sh …` / `sudo /opt/houses/switch.sh` — no scp,
no arbitrary sudo. So `release.sh` (running as root) installs the ref's
own `units/*`, `network-watchdog.sh`, `switch.sh` and `run-instance.sh`,
`daemon-reload`s and (re)enables the watchdog timer on every release;
the CI-built frontend dist rides the exec channel on stdin (`-p
/dev/stdin`), never scp. `release.sh` itself re-execs the ref's copy
after checkout, so every release both ships and applies its own tooling.
Until the next release, a rollback runs the box's older `switch.sh`
(still functional; CLI `.backup` snapshot — bounded only after shipping).

### Forward-release order

1. Landed R1–R6 in the repo + tests (PRs #106–#112).
2. Verified locally: the skip-build boot path, the stdin dist protocol both
   ways, the FIFO teardown order, the `dash -n` parse.
3. **Shipped 2026-09-12: `v1.4.6` deployed through the hardened pipeline +
   switch → prod on green (`85583a1`). New `/health` reads
   `{status, db, last_write}` with a fresh write timestamp; box memory
   right after the flip: 424 MiB free.**
4. Four pipeline bugs found and fixed in-release (`v1.4.0` missing
   checkout, `v1.4.1` non-idempotent guard, `v1.4.2` scp vs the
   command-restricted key, `v1.4.3` bash-only process substitution,
   `v1.4.4` FIFO teardown order, `v1.4.5` low-memory gate refusal) —
   each caught by a guardrail, none touched prod.
4. Switch dispatch → prod on current main with the `/health` db/last-write
   probe and release logs. Watch `/health` and box CPU until the
   first-boot cascade converges (Sep-5 precedent: ~1 h).
5. Record the outcome here.

## Release log retention

`/opt/houses/logs/releases/` keeps the newest 32 runs of each of
`release-*` and `switch-*`; older files are deleted by the scripts at the
end of every successful run. journald's own size cap applies to the
`houses-release` tag (systemd default: 10% of the volume / 4 weeks — raise
`SystemMaxUse` in `/etc/systemd/journald.conf.d/` if a box needs more
history).
