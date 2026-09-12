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
