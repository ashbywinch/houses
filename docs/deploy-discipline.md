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

## Release log retention

`/opt/houses/logs/releases/` keeps the newest 32 runs of each of
`release-*` and `switch-*`; older files are deleted by the scripts at the
end of every successful run. journald's own size cap applies to the
`houses-release` tag (systemd default: 10% of the volume / 4 weeks — raise
`SystemMaxUse` in `/etc/systemd/journald.conf.d/` if a box needs more
history).
