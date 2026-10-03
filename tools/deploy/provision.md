# Provisioning the houses boxes — the operator walkthrough

Everything that can be scripted lives in `tools/deploy/` and `terraform/`; the
Release workflow drives it. This file is the part only you can do, in order.
Read [docs/anti-fragile-rollout-plan.md](../../docs/anti-fragile-rollout-plan.md) first —
it explains *why* the design is shaped this way, and
[docs/deploy-discipline.md](../../docs/deploy-discipline.md) has the operating rules.

The layout this all targets:

```
                     static IP (houses-static, regional)
                                │  address of
                                ▼
                 L4 forwarding rules (houses-l4-http :80, houses-l4-https :443)
                                │  target = the ACTIVE target instance
                                ▼
     ┌────────────────────┐    ┌──────────────────────┐
     │ instance: houses   │    │ instance: houses-standby
     │ (one role may be   │    │ (the other role)      │
     │  the rule's target)│    │                       │
     │ Caddy :443         │    │ Caddy (nothing        │
     │ app :8765          │    │  external: smoke is   │
     │ /opt/houses/       │    │  127.0.0.1)           │
     │   app  data  logs  │    │                       │
     └────────────────────┘    └──────────────────────┘
        ephemeral IP (SSH)          ephemeral IP (SSH)
```

- **The static IP belongs to the forwarding rules**, never to an instance.
  Instance addresses are ephemeral and exist for SSH/control only.
- **Roles rotate with the rules' target.** "Who is live" is that target, and
  nothing else. Both instances are fixed resources.
- **Only 22, 80 and 443 are open.** The app port (8765) is loopback-only.

---

## 1. One-off prerequisites (console + local CLI)

1. **Project + billing**: the `houses` GCP project (free tier: one e2-micro +
   30 GB in us-west1/us-central1/us-east1).
2. **gcloud on your machine**:
   ```bash
   gcloud auth login
   gcloud config set project <project-id>
   ```
3. **The operator key** (your break-glass shell; the same key installs as
   `ubuntu`'s authorized key on both boxes):
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/houses_operator -N "" -C "houses-operator"
   ```
4. **The CI deploy key** (never a login — it is forced through the allowlist):
   ```bash
   ssh-keygen -t ed25519 -f houses-deploy -N '' -C deploy@houses
   ```
5. **The Terraform state bucket** (Terraform cannot create its own):
   ```bash
   gsutil mb -l us-west1 -b on gs://houses-tfstate
   ```
6. **The artifact + seed buckets** (Terraform manages `houses-artifacts`; the
   seed bucket is yours):
   ```bash
   gsutil mb -l us-west1 -b on gs://houses-seed
   ```
7. **The box service account's permissions** are Terraform's job
   (`houses-box-deploy`); the **CI** service account needs, once:
   - `roles/compute.instanceAdmin.v1`, `roles/compute.networkUser` (flip the rules)
   - `roles/storage.objectAdmin` on `gs://houses-artifacts` (upload artifacts)
   - access to the `houses-tfstate` backend
   ```bash
   SA=<ci-sa-email>
   gsutil iam ch serviceAccount:$SA:objectAdmin gs://houses-artifacts
   gsutil iam ch serviceAccount:$SA:objectAdmin gs://houses-tfstate
   ```

## 2. The greenfield scaffold (the one-time creation — via the workflow)

The rollout machinery cannot start on an empty project: `resolve` needs both
boxes, both target instances and both L4 rules to exist. Creating them is the
**`scaffold` action** — never a hand-run terraform.

```bash
# 0. the base image must exist first — bake it (the builder is stock-image,
#    the only resource the bake touches):
gh workflow run Release -f action=bake
#    wait for the run: builder TERMINATED -> houses-base created

# 1. build the artifact the fresh boxes bootstrap from (optional but usual —
#    a bare scaffold still works; the first release fills the boxes):
gh workflow run Release -f action=build -f ref=main

# 2. create the two boxes + target instances + rules:
gh workflow run Release -f action=scaffold -f artifact=gs://houses-artifacts/<sha256>.tar.gz
```

The scaffold applies the whole config: the two instances boot from
`houses-base` (default `base_image` — a missing image fails the job loudly:
run `action=bake` first; there is no stock fallback for a box), each renders
`tools/deploy/box-bootstrap.sh` into `startup-script` metadata and builds
itself: packages → operator key → artifact (instance SA, sha256 verified) →
layout/units/sudoers → deploy-key allowlist → seed restore → migrations → app
env → Caddy → markers. The action refuses if the L4 rules already exist —
from then on it is `action=release`, every time.

Watch a bootstrap with
`ssh -i ~/.ssh/houses_operator ubuntu@<ephemeral-ip> "sudo tail -f /var/log/syslog"`
or `gh workflow run Release -f action=diagnose`.

## 2b. Adoption — from today's single box to the two-instance data plane

One-off. There are two variants, and which one you take depends on whether the
existing box's database is trustworthy. **Read the warning first.**

> **Ordering.** The rollout's tooling ships inside the artifact, and the new
> scripts have no per-box flip. So the adoption comes BEFORE the next `release`:
> terraform first, then the box work, then the gated `cutover`. A box still on the
> old layout cannot be flipped by this tree at all.
>
> **The old box is NOT adopted in place.** Its disk keeps the old OS, its sudoers
> and its SSH allowlist know only the old command shapes, and it has no
> `install-artifact.sh`. The new config therefore builds FRESH instances and the
> old box is either left alone (variant A, where it serves until the flip) or
> abandoned where it stands (variant B). Terraform is told this with `moved`
> blocks and `ignore_changes = [boot_disk]`, so an apply can never replace or
> re-image an existing box — only `-replace` (the rollout) creates a new one.

**Variant A — the live database is trustworthy (the normal adoption).**

1. Run `action=scaffold`. The two new instances (`houses`, `houses-standby`)
   are created from the base image; the existing box is left exactly as it is.
   `houses-static` stays attached to IT for now (two pieces of infrastructure
   cannot own one address), so traffic keeps flowing to the old box.
2. `gh workflow run Release -f action=release` — builds the artifact, rebuilds
   `houses-standby`, installs and smokes it.
3. Publish the live database to the new plane — this is the step that needs the
   old box, so run it from the LAN machine:
   `tools/deploy/seed-box.sh <old-box-ip>` (pulls the live DB, migrates the copy
   with the rollout's runner, uploads the seed).
4. **`action=recover`** with that seed: `houses-standby` is restored from it,
   migrated, started and flipped to. From here the L4 rules own the address...
   which the old box still holds. So before the flip, detach it:
   ```bash
   gcloud compute instances delete-access-config <old-box> \
     --access-config-name=external-nat --zone us-west1-a
   ```
   and run `terraform apply` once more so the rules claim `houses-static`
   (accepted downtime: the seconds the address is unattached).
5. Verify: `https://houses.blueumbrella.net/health`, `terraform output live_target`.
6. Retire the old box when you are satisfied: `gcloud compute instances delete <old-box>`
   — never before, because it is the copy of record until the flip is verified.

**Variant B — the live database is NOT trustworthy (today's case).** Skip the
snapshot of the old box entirely; its data is abandoned:

1. Run `action=scaffold`, then detach the address from the old box and re-run
   `action=scaffold`'s apply equivalent (or apply once the address is free) so
   the rules hold it — the old box may keep serving on an ephemeral address,
   or not serve at all; that is the point.
2. `action=release` → build + rebuild the standby + install + smoke.
3. `action=recover -f snapshot=gs://houses-seed/latest.db` → the standby is
   restored from the human-validated seed (or from any object you name), migrated,
   started, and flipped to. **Every write since that seed is gone** — the
   approval gate is where you accept that.
4. Verify and retire the old box as above. Its data is never read, and the next
   rollout replaces it anyway.

## 3. DNS + TLS (Cloudflare terminates; the box serves an origin certificate)

`blueumbrella.net`'s DNS lives at **PointHQ** (`dns4.pointhq.com` /
`dns10.pointhq.com`). One **A record**, proxied through Cloudflare, is all DNS
needs:

- `houses.blueumbrella.net` → the **static** address (`terraform output address`)

There is no smoke hostname: only the rule's target receives 80/443, and the
standby's smoke is local (127.0.0.1).

**TLS is Cloudflare's job, not the box's.** Browsers see Cloudflare's edge
certificate; the Cloudflare → origin leg uses a **Cloudflare Origin
Certificate** — a 15-year certificate issued by Cloudflare's own CA that never
rotates. Deliberately NOT Let's Encrypt: a rollout builds a fresh box, and a
fresh box has no certificate, so ACME would re-issue on every rollout and hit
Let's Encrypt's "5 duplicate certificates per week" limit within days.

Setup, once (Cloudflare dashboard):

1. **SSL/TLS → Overview → mode: Full (strict).** With Flexible the origin leg
   would be plaintext, and nothing on the box listens on :80 any more.
2. **SSL/TLS → Origin Server → Create Certificate** for
   `houses.blueumbrella.net`, then upload the pair to the bucket the box already
   reads with its instance identity:
   ```bash
   gsutil cp origin.pem gs://houses-seed/cloudflare/origin.pem
   gsutil cp origin.key gs://houses-seed/cloudflare/origin.key
   ```
   (No new IAM: the box's service account already reads that bucket.)

`install-caddy.sh` fetches the pair on every box, writes it to
`/etc/caddy/certs/`, and serves it for the hostname — reverse-proxying
`127.0.0.1:8765`. Verify from outside your network (phone on cellular):
`https://houses.blueumbrella.net/health` → `{"status":"ok"}`.

**Gotcha:** if the origin certificate is missing from the bucket, the bootstrap
FAILS loudly rather than serving a box that cannot be reached over HTTPS.

## 3b. The LAN scrape worker (Chrome lives at home, not on the box)

The box enqueues scrape jobs; the LAN machine's worker completes them with
exponential backoff via the queue. Proper service install, not a manual loop:

```bash
sudo HOUSES_SCRAPE_APP_URL=https://houses.blueumbrella.net \
  bash tools/deploy/install-lan-worker.sh
```

This installs two boot-enabled systemd units:

- `houses-chrome.service` — shared headless Chrome on :9222 (the LAN dev app and
  the worker reuse the same instance)
- `houses-scrape-worker.service` — polls the box's queue, scrapes, reports
  (`Restart=always`; logs: `journalctl -u houses-scrape-worker -f`)

The worker mints its auth cookie from the LAN `.env`'s
`HOUSES_SESSION_SECRET` — the same secret the box has. If the LAN machine is off,
the queue simply holds jobs with backoff and the worker drains them on return: no
data loss, only scrape latency.

## 4. Google OAuth — allow the app's hostnames

In the Google Cloud console, OAuth consent screen → **Authorized redirect URIs**
for the web client in `/etc/houses.env` (same project, no new credentials):
- `https://houses.blueumbrella.net/api/auth/callback`
- `https://houses-smoke.blueumbrella.net/api/auth/callback` — the rollout's
  review surface. The app builds the login redirect from the hostname each
  request arrived on (allowlisted by `HOUSES_PUBLIC_URL` / `HOUSES_REVIEW_URL`
  / `HOUSES_FRONTEND_URL`; `HOUSES_REVIEW_URL` defaults to the smoke host), so
  a reviewer signs in ON the review hostname while the box already carries its
  final production role URL.

## 4b. Production guard (applied automatically by box-setup.sh)

The box's sudoers grants ONLY `/opt/houses/install-artifact.sh`,
`/opt/houses/switch.sh`, and read-only `journalctl` — no interactive login can
restart app units or mutate the deployment. Production changes go only through
the Release workflow (build → install → cutover). If that feels slow, improve
the process; never ssh in and "just fix it" directly.

## 5. GitHub secrets

Repo → Settings → Secrets and variables → Actions:

```
BOX_USER          ubuntu
BOX_SSH_KEY       the PRIVATE half of the restricted deploy key (houses-deploy)
DEPLOY_PUBKEY     its PUBLIC half (installed with the forced-command allowlist)
OPERATOR_PUBKEY   your admin key's PUBLIC half (break-glass: shell + sudo)
GOOGLE_SA_KEY     base64 of the CI service-account JSON (see §1.7)
```

There is no `BOX_HOST`: the workflow reads both instances' ephemeral addresses
from GCP (the rule's target tells it which is which).

Then install the allowlist entry automatically on each box: the bootstrap does it
from `DEPLOY_PUBKEY` (or, by hand, as root:
`/opt/houses/install-deploy-allowlist.sh "$(cat houses-deploy.pub)"`).

Sanctioned remote commands (everything else = silent no-op):

```
sudo /opt/houses/install-artifact.sh gs://<bucket>/<sha256>.tar.gz   — install the artifact on this box
sudo /opt/houses/switch.sh --snapshot                               — FREEZE production, then stream its DB to stdout
sudo /opt/houses/switch.sh --unfreeze                               — the abort path: serve from here again
sudo /opt/houses/switch.sh --rebase <rows>                          — restore the snapshot on stdin, verify, migrate, start
sudo /opt/houses/switch.sh --restore gs://<bucket>/<object>.db      — restore THIS database instead (the recovery exception)
sudo /opt/houses/switch.sh --diagnose                               — read-only state dump
```
`journalctl` is NOT in the allowlist: CI does not use it, and a human logs in with the
operator key and reads logs directly. Nothing else is forwarded — an unknown command
is a silent no-op, so a shape that is not listed here does nothing at all.

## 5b. Human prod gate — the DOUBLE gate on every traffic move

Traffic moves (`cutover`, `flip`, `recover`) are held by TWO independent locks;
either one missing means the job refuses:

1. **The GitHub environment gate.** Repo → Settings → Environments →
   **production** → Protection rules: **Required reviewers** = you,
   "Allow administrators to bypass" = false (CHECK this is actually configured
   — on 2026-10-02 the environment had NO protection rules at all, so GitHub
   autopassed the declared gate and a flip ran without any approval).
2. **The dispatch's approval input.** The three jobs' FIRST step fails loudly
   unless the dispatch carried `-f approval=approved`. A traffic move can never
   silently skip or autopass this: no approval in the dispatch → nothing runs,
   not even a box command. The command examples in §6 all carry it.

`rollback` stays ungated: it is the unapproved emergency undo.

## 6. A rollout (the whole loop)

Every rollout **builds a fresh box** (replace-not-repair): the standby instance is
replaced from the artifact, so no box drifts, and the bootstrap path is exercised
on every rollout — the standby is the DR drill.

```bash
# 1. build the artifact, rebuild the STANDBY instance from it, install + smoke
gh workflow run Release -f action=release -f ref=main

# 2. read the run log — every verdict line:
#    the new box: seed restored → migrations applied+checked → artifact receipt
#    the install:  sha256 verified (or "already runs this artifact"), venv receipt
#                  ok, `migrations: N applied+checked, 0 failed`, app healthy,
#                  smoke ok, then INSTALL READY with the app STOPPED.

# 3. the flip — the dispatch carries the approval input, and the job waits at
#    the `production` environment gate for your reviewer approval
gh workflow run Release -f action=cutover -f ref=main -f approval=approved

# 4. something wrong? undo in one command (no DB restore: the rollout never
#    writes the owner's database)
gh workflow run Release -f action=rollback -f ref=main

# 5. re-install onto the existing standby (no rebuild — a retry after a failed
#    install, or a probe)
gh workflow run Release -f action=install -f artifact=gs://houses-artifacts/<sha256>.tar.gz

# 6. the owner's data is not trustworthy? RECOVER-PREP onto a named object
#    instead of carrying it forward — UNGATED (restore + review surface only):
gh workflow run Release -f action=recover -f snapshot=gs://houses-seed/latest.db
#    then, after reviewing the served box, promote with the GATED flip (§7b):
gh workflow run Release -f action=build -f ref=main
gh workflow run Release -f action=flip -f approval=approved -f artifact=gs://houses-artifacts/<sha256>.tar.gz
```

What each phase owns:

| Phase | Data | Proves |
|---|---|---|
| `provision` (bootstrap) | **seed + migrations** | the box builds from an artifact and a human-validated seed |
| `install` (rehearsal + smoke) | the same seed-derived DB | the artifact boots, serves and migrates |
| `cutover` → freeze + `--snapshot` | **the owner's live DB**, app stopped | the exact last state production served (no write can land after the freeze) |
| `cutover` → `--rebase` | that snapshot, restored | integrity + row count match, migrations + checks pass |
| `cutover` → `set-target` + settle | — | the public site on the new owner |

**Every cutover first ARCHIVES the clean pre-migration snapshot** (`gs://houses-artifacts/snapshots/<timestamp>.db`, written by CI, pruned after 90 days) and only then lets the runner migrate the restored copy — with the runner's own pre-backup (`<db>.pre-<migration>`) kept beside the database on the box. The process never runs a migration without a clean copy that still exists afterwards; the 2026-09-24 lesson was precisely that there was none.

**The cutover is the only downtime, and it is the freeze window.** `--snapshot`
stops the owner's app *before* copying, so nothing is lost between the copy and
the flip — the price is that production is down from the freeze until the flip
(snapshot → transfer → restore → verify → migrate → start → flip; minutes on a
2.3 GB database). Everything that can happen earlier does: the box is built,
installed and smoked before the gate is even opened. If any step after the
freeze fails, CI restarts the owner's app (`--unfreeze`) and traffic never moved.

**The "settle gate"** is the last step of the cutover: five minutes of continuous
public polling of `https://houses.blueumbrella.net/health` on the new owner. It
hard-fails if the site does not answer, or if `status`/`db` are not `ok`, or if
`last_write` is missing — i.e. if the box is not serving. It reports `last_write`
staleness but does not gate on it: `last_write` is `MAX(created_at)` in
`node_results`, so a converged DAG that has nothing to recompute legitimately
writes nothing for minutes at a time.

The seed is *not* the correct database and is not meant to be: it only gives a
fresh box something to boot and smoke with. The correct database can only land at
the flip, because the owner keeps writing up to it — see §7.

## 7. The seed, and when the box gets the real data

**What a seed is.** `gs://houses-seed/latest.db` — one full copy of the production
SQLite database (the DAG's computed state, ~2.3 GB), produced by a human running
`tools/deploy/seed-box.sh`: it pulls the owner's DB, applies the migrations to the
copy, verifies, and uploads. `box-bootstrap.sh` restores it to
`/opt/houses/data/houses.db` on a fresh box and writes `/opt/houses/SEED`
(object, etag, when, row count) so the box's origin is a file, not a memory.

**Why it is not "the correct database".** The live database is being written
continuously, so any copy is stale the moment it is taken. Baking a copy in at box
build time would mean serving data that is 10–15 minutes old at the flip and
silently dropping every write in between. That is why the plan puts the data step
immediately before the traffic step: the cutover takes a **fresh snapshot of the
owner**, streams it to CI, restores it onto the standby with the app stopped,
verifies it (`integrity_check` + row count against the snapshot's own count),
runs the migrations + checks on that quiescent copy, and only then starts the app
and moves the rule. The seed's data is superseded before a single request reaches
the box.

**Refresh the seed deliberately**, when you judge the settled state trustworthy —
it is never auto-refreshed (the old `--publish` step that did that made the newest
live output the next restore source automatically, which is how a stale capture
became production's data on 2026-09-24):

```bash
tools/deploy/seed-box.sh <owner-ip>       # copy → migrate the copy → verify → upload
```

## 7b. Recovery: when the owner's data must NOT be carried forward

The normal path carries the live database forward (freeze → snapshot → restore →
flip). This is the exception, for when that database cannot be trusted — a
migration that never ran, a stalled cascade, a botched restore, a box that is
simply broken. Do not snapshot it and do not "carry it forward and fix it
later".

Recovery is TWO steps, and the split is the point: **everything that touches
the box happens BEFORE the approval**. `action=recover` is ungated PREP — it
installs the current artifact (app + tooling), writes the FINAL production
role URL, restores the named object, starts the app and waits until the
review surface actually serves. The approval then
comes at the flip, which moves the traffic rules and retires the smoke relay
— and touches nothing on the approved box (2026-10-02: recover used to
restore AFTER its gate; 2026-10-03: the flip used to install tooling and
rewrite the role URL after the gate. Both meant the box that went live was
never exactly the box that was approved).

The box is reviewed through `houses-smoke.blueumbrella.net` (the owner relays
to it) even though its role URL is production by then — the approval is for
the REAL thing, not a rehearsal. Login on the review hostname works because
the app builds the OAuth redirect from the hostname each request arrived on
(see §4) — no URL change, no restart, nothing after approval.

```bash
# 1. PREP (ungated): install the current artifact, write the production role
#    URL, restore the named object, start the app, verify the surface serves.
#    No traffic change; the owner is untouched and keeps serving production.
gh workflow run Release -f action=recover -f snapshot=gs://houses-seed/latest.db

# 2. review the FINAL box at https://houses-smoke.blueumbrella.net, then
#    PROMOTE — the flip changes nothing on it:
gh workflow run Release -f action=flip -f approval=approved -f ref=main
#    approve the production environment gate — that approval is where the
#    trade is accepted: every write since the object was made is gone.
```

`action=flip` retires the smoke relay on the abandoned owner and moves both
L4 rules to the approved box, then probes public health. No install, no
tooling, no role URL, no restart, no DB op on the approved box. The owner is
never read.

Choosing the source is a human decision made once, in the incident:

- `gs://houses-seed/latest.db` — the human-validated seed (see §7). This is the
  default and the usual answer.
- A copy someone took deliberately, e.g. a database exported off a box before
  the problem appeared. Name it explicitly; the workflow takes exactly the
  object you name.

Then: the recovered box is the owner. The abandoned box becomes the standby and
the **next rollout replaces it** (`provision`), so its data cannot come back.
Note that `rollback` is NOT the undo here — it would move traffic back onto the
data you just abandoned; the undo is another recovery — `recover` then `flip` —
with a different object.

**Recovery is verified by the rollout's own mechanism**: the restore's
`integrity_check` + row count, the runner's `migrations: <N> applied+checked,
0 failed` verdict (the box refuses to start the app without it), the smoke, and
the settle window.

## 8. Backups — where they live, and how to tell which are good

**Where the backups are:**

| What | Where | When it exists |
|---|---|---|
| the **seed** (the recovery artifact) | `gs://houses-seed/latest.db` (+ `.meta`); the bucket is VERSIONED, so publishing a new seed never destroys the previous generation (last 10 kept) | made by `seed-box.sh`, replaced deliberately |
| a box's **live database** | that box's `/opt/houses/data/houses.db`, with each migration's clean pre-backup kept beside it as `<db>.pre-<migration>` | while the box exists (see `/opt/houses/RESTORED` for what it was last restored from) |
| the LAN dev copy | `data/houses.db` on this machine | the development data — never a recovery source |
| **the cutover's clean snapshots** | `gs://houses-artifacts/snapshots/<timestamp>.db` — every cutover's PRE-MIGRATION live DB, archived before the runner migrates the restored copy | one per cutover, pruned after 90 days |
| a deliberately chosen recovery source | `gs://houses-seed/recovery-<timestamp>.db` — copies the operator has verified and NAMED for a specific recovery | made by hand, like this session's |

Versioning and pruning are CONFIGURED on the buckets (GCS lifecycle), not a script: the normal process therefore always keeps the clean copy it took, and the archive cannot grow without bound.

There is deliberately no pile of floating copies beyond those intentional archives: the plan's rule is that a fresh
box's data is **seed + migrations**, and that the seed is the human-validated
artifact. Nothing else is kept, so "which backup?" has exactly one normal answer.

**How to know a backup is good — without trusting anyone's memory:**

```bash
tools/deploy/verify-backup.sh gs://houses-seed/latest.db
```

It copies the object, then applies the SAME gates the rollout insists on:
`PRAGMA integrity_check`, a non-zero `node_results` row count, and the migration
runner's verdict `migrations: <N> applied+checked, 0 failed`.

**`MIGRATION-COMPATIBLE` is the only claim the script ever makes: the tooling
can carry this object forward.** It is NOT a statement that the data is what you
want — the migration machinery repairing a copy proves nothing about the copy's
source (see 2026-09-24). **Trust is a human judgement**, made from the meta's
`captured_from`/`captured_at`/`made_by` plus your knowledge of that moment, and
it is taken at the `recover` approval gate — never stamped by a script.

`latest.db.meta` (next to the seed) records the inputs to that human judgement
without needing the 621 MB copy: `object`, `captured_from` (which box),
`captured_at`, `rows`, `manifest_sha256`, `migrations_applied`,
`gates=passed-on-copy`, `made_by`, and `trust=unset` until a human takes the
decision. It is written by `seed-box.sh` at upload time.

On a box, `/opt/houses/RESTORED` answers "this box's data came from X at time T
with N rows" and `/opt/houses/SEED` answers what the box bootstrapped from.
`action=diagnose` prints both.

## Gotchas (learned the hard way)

- **The rule's target is the only "who is live" fact.** `terraform apply` never
  changes it (the rules ignore changes to `target`); only the workflow's
  `set-target` does. If you ever edit traffic by hand, you have moved production.
- **Both L4 rules must target the same instance.** The workflow refuses to
  proceed when they disagree, and `--diagnose` prints the box's own state.
- **Never attach the static address to an instance.** It belongs to the rules;
  an instance's address is ephemeral and only for SSH.
- **`HOUSES_PORT` in `/etc/houses.env` is ignored** — `run-instance.sh` sets 8765
  (the app's single port, the same for both roles).
- **The standby's app must be stopped while anything writes its database.** The
  install and the rebase both stop it themselves; don't start it by hand.
- **The LAN `.env` still contains sheet-era keys** (`HOUSES_SHEET_ID`,
  `GOOGLE_SHEETS_SERVICE_ACCOUNT`) — they crash pydantic at boot
  (`extra_forbidden`). Strip them when refreshing `gs://houses-seed/houses.env`.
- **Rollback moves traffic; it does not restore data.** The rollout never writes
  the owner's database, so the previous owner is still serving its own untouched
  state.
- **Break-glass** if the workflow itself is unusable: the operator key (shell +
  sudo) on the ephemeral IP, the GCP serial console for a box that will not boot,
  or `gh workflow run Release -f action=provision` to rebuild the standby.
