# Provisioning the houses blue/green box — your manual walkthrough

Everything that can be scripted lives in `tools/deploy/` (release.sh, switch.sh,
run-instance.sh, units/). This file is the part only you can do, in order.
Time: ~1–2 hours spread over a couple of sittings (A1 capacity can take a day
of retries). Do NOT do step 1 in the same sitting as a release you care about.

The box layout this all targets:

```
/opt/houses/
├── ACTIVE            # "blue" | "green" — who serves the live DB
├── PREVIOUS          # the side before the last flip (rollback target)
├── data/             # LIVE data: houses.db, caches, CSVs (shared)
├── blue/             # checkout A — port 8765
├── green/            # checkout B — port 8766
├── blue-smoke.db     # standby A's snapshot copy (created by release.sh)
├── green-smoke.db    # standby B's snapshot copy
├── run-instance.sh   # (from tools/deploy/)
├── release.sh        # (from tools/deploy/)
└── switch.sh         # (from tools/deploy/)
```

Public traffic flows Cloudflare Tunnel -> localhost ports; the VCN never
exposes 8765/8766. **Only SSH (22) is open to the internet.**

---

## 1. Google Cloud box — Terraform (account + gcloud login are the only manual bits)

The whole GCP side (VPC, SSH-only firewall, e2-micro instance, static IP,
startup-script box setup) is `terraform/` in the repo. Google's free tier
here is **permanent** — one e2-micro (1 vCPU / 1 GB RAM) + 30 GB disk in
us-west1/us-central1/us-east1, always-on, no sleep, no idle-reclaim
policy. The app alone runs in ~100 MB; Chrome is NOT on this box (the
Rightmove scraper lives on your LAN — see the worker in Step 4).

1. **Create the account** at cloud.google.com (**Start free**; a billing
   account is required for the free tier but e2-micro + 30 GB stay free).
2. **gcloud CLI + login** (this machine):
   ```bash
   # install: https://cloud.google.com/sdk/docs/install — or snap/apt
   gcloud auth application-default login   # browser OAuth, no key files
   gcloud config set project <project-id>  # from the console project picker
   ```
3. **The SSH key** for the box (this machine):
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/oracle -N "" -C "oracle-houses"
   ```
4. **Fill the variables** and apply (terraform already installed on this
   machine):
   ```bash
   cd terraform
   cp terraform.tfvars.example terraform.tfvars   # fill project (+ region/zone)
   terraform init
   terraform plan     # read it — firewall is SSH-only, machine is e2-micro
   terraform apply
   terraform output ssh_command   # -> ssh -i ~/.ssh/oracle ubuntu@<ip>
   ```
   `apply` runs the startup script: apt deps, Caddy, uv, the two
   checkouts (/opt/houses/blue + green), units, ACTIVE=blue. ~5–10 min
   after boot (watch: `ssh ubuntu@<ip> "sudo tail -f /var/log/syslog"`).

## 2. Secrets + data cutover (the manual part that stays manual)

1. **Install the secrets**: the LAN `.env` as root-only `/etc/houses.env`,
   using the cutover pipeline from `docs/deployment-oracle-free-tier.md`
   Phase 3 (grep out the sheet-era keys — they crash pydantic at boot).
   Critical keys must be present and non-empty:
   ```bash
   sudo install -o root -g root -m 600 /dev/stdin /etc/houses.env   # STRICT KEY=VALUE
   # required: HOUSES_SESSION_SECRET, HOUSES_GOOGLE_WEB_CLIENT_ID/SECRET,
   #           HOUSES_GOOGLE_DEVICE_CLIENT_ID/SECRET, TFL_API_KEY,
   #           HEIGIT_API_KEY, PLACES_API_KEY, EPC_BEARER_TOKEN
   # plus:    HOUSES_RIGHTMOVE_SCRAPER_OFFLINE=true   (no Chrome on the box)
   ```
   **Do not put HOUSES_PORT in /etc/houses.env** — run-instance.sh sets it
   per side (8765/8766). Add the host vars too (Step 3's env block).
2. **Copy the live data + DB** (from the LAN machine, `make stop` the LAN
   app first — same machinery as the plan doc Phase 3; the DB is ~520 MB
   now that it is compressed):
   ```bash
   sqlite3 data/houses.db ".backup '/tmp/houses-backup.db'"
   cat /tmp/houses-backup.db | ssh ubuntu@<ip> "umask 077; cat > /opt/houses/data/houses.db && chmod 600 /opt/houses/data/houses.db && sqlite3 /opt/houses/data/houses.db 'PRAGMA integrity_check;' | grep -q '^ok$'"
   rsync -a --exclude 'houses.db*' data/ ubuntu@<ip>:/opt/houses/data/
   rm -f /tmp/houses-backup.db
   ```
3. **Start the live side**:
   ```bash
   ssh ubuntu@<ip> "sudo systemctl enable --now houses-blue && curl -s --max-time 10 -o /dev/null -w 'blue: %{http_code}\n' http://localhost:8765/health"
   ```
4. **Install the scrape worker on the LAN** (where Chrome lives — the box
   enqueues scrape jobs, the worker completes them with exponential
   backoff via the queue). Proper service install, not a manual loop:
   ```bash
   sudo HOUSES_SCRAPE_APP_URL=https://houses.blueumbrella.net \
     bash tools/deploy/install-lan-worker.sh
   ```
   This installs two boot-enabled systemd units:
   - `houses-chrome.service` — shared headless Chrome on :9222 (the dev
     app and the worker reuse the same instance)
   - `houses-scrape-worker.service` — polls the box's queue, scrapes,
     reports (Restart=always; logs: `journalctl -u houses-scrape-worker -f`)
   The worker mints its auth cookie from the LAN `.env`'s
   HOUSES_SESSION_SECRET — the same secret the box has. If the LAN machine
   is ever off, the queue simply holds jobs with backoff and the worker
   drains them on return — no data loss, only scrape latency.

## 3. DNS + HTTPS (PointHQ A records + Caddy — no Cloudflare needed)

The domain is blueumbrella.net and its DNS lives at **PointHQ**
(`dns4.pointhq.com` / `dns10.pointhq.com`). Log into your PointHQ account
(or the registrar console — `whois blueumbrella.net` shows the registrar if
you don't know your PointHQ login). The box has a static IP, so two plain
**A records** are all DNS needs:

- `houses.blueumbrella.net` → `<box public IP>` (from `terraform output`)
- `houses-smoke.blueumbrella.net` → `<box public IP>` (same IP)

Caddy is ALREADY on the box (the terraform startup script installs it; on
an existing box run `/opt/houses/install-caddy.sh` once). It terminates
HTTPS with automatic Let's Encrypt certs and reverse-proxies:

- `houses.blueumbrella.net` → `127.0.0.1:8765` (the ACTIVE side)
- `houses-smoke.blueumbrella.net` → `127.0.0.1:8766` (the standby)

Ports are role-based, so the Caddyfile is static forever — a blue/green
flip never touches TLS or DNS. Hostnames come from `/etc/houses.env`
(HOUSES_MAIN_HOST / HOUSES_SMOKE_HOST) or defaults; add them if you want
non-default subdomains:

```bash
sudo sh -c 'echo "HOUSES_MAIN_HOST=houses.blueumbrella.net" >> /etc/houses.env'
sudo sh -c 'echo "HOUSES_SMOKE_HOST=houses-smoke.blueumbrella.net" >> /etc/houses.env'
```

First-time cert issuance happens automatically once the A records resolve
(Caddy retries in the background — `journalctl -u caddy`). Verify from
outside your network (phone on cellular):
`https://houses.blueumbrella.net/health` → `{"status":"ok"}`.

**Gotcha:** if Caddy started before the A records propagated, its initial
cert attempt failed and it serves HTTP-only until it retries (background
backoff — can take a while). Fix instantly: `sudo systemctl restart caddy`.

## 4. Google OAuth — allow the prod hostnames

In the Google Cloud console, open the OAuth consent screen → **Authorized
redirect URIs** for the web client you already use (the LAN `.env`'s
HOUSES_GOOGLE_WEB_CLIENT_ID/SECRET — same project, add URIs; no new creds):
- `https://houses.blueumbrella.net/api/auth/callback`
- `https://houses-smoke.blueumbrella.net/api/auth/callback`

## 4b. Production guard (applied automatically by box-setup.sh)

The box's sudoers grants ONLY `/opt/houses/release.sh`, `/opt/houses/
switch.sh`, and read-only `journalctl` — no interactive login can restart
app units or mutate the deployment. Production changes go only through the
Release workflow (tag → deploy to standby → smoke → switch). If that feels
slow, improve the process; never ssh in and "just fix it" directly.

## 5. GitHub secrets for the release workflow

Repo → Settings → Secrets and variables → Actions:
- `BOX_HOST` — the Oracle public IP (or hostname)
- `BOX_USER` — `ubuntu`
- `BOX_SSH_KEY` — the private half of a **restricted deploy key** (not your
  personal key). The box entrypoint dispatches it through a strict allowlist
  (see below). Generate a dedicated keypair once per box:
  `ssh-keygen -t ed25519 -f houses-deploy -N '' -C deploy@houses`.

Then ON THE BOX (as root) install the allowlist entry — do NOT hand-edit
authorized_keys with the old `$1` recipe; sshd does not populate positional
params in forced commands, so that entry silently swallowed every
arg-bearing invocation (the 2026-09-23 `--rollback`/`--diagnose` no-ops).
The dispatcher matches on `$SSH_ORIGINAL_COMMAND` and forwards ONLY the
sanctioned shapes:

```sh
sudo /opt/houses/install-deploy-allowlist.sh "$(cat houses-deploy.pub)"
```

Sanctioned remote commands (everything else = silent no-op):

```
sudo /opt/houses/release.sh <ref>       → deploy to standby
sudo /opt/houses/switch.sh              → flip
sudo /opt/houses/switch.sh --rollback   → undo last flip
sudo /opt/houses/switch.sh --diagnose   → read-only box state dump
sudo journalctl -u <unit> -n <N> --no-pager   → read-only logs
```

Set the private key text as the `BOX_SSH_KEY` secret.

## 5b. Human prod gate (GitHub Environments)

Repo → Settings → Environments → **production** → Protection rules:
**Required reviewers** = you. Deletion? Leave "Allow administrators to
bypass": false. The Release workflow's `switch` job declares
`environment: production`, so every traffic flip now waits for your
explicit approval on GitHub (the sign-off requirement from the
2026-09-23 governance breach). `rollback` stays ungated: it is the
emergency undo.

## 5c. Provision-from-GitHub (GCP, replace-not-repair)

One dispatch builds a fresh box from code and verifies it before anything
else:

```sh
tools/deploy/seed-box.sh        # run on the LAN machine: pull live DB ->
                                # migrate the COPY (the rehearsal) ->
                                # upload gs://houses-seed/latest.db
gh workflow run Release --ref main -f action=provision
# then: action=deploy (standby smoke on the new box), the production
# environment approval, action=switch. Retire the old instance only after
# the new box serves:
#   gcloud compute instances delete houses   (project houses-498215, zone us-west1-a)
```

The provision action: renders `tools/deploy/box-bootstrap.sh` as the GCP
startup-script, launches `houses-rebuild` (e2-micro, houses tags, in the
repo's VPC), waits until the deploy key can reach the box (the tooling
banner — the allowlist silent-no-ops before that, so no false positive),
and registers the box's IP as the `BOX_HOST` repo variable. The bootstrap
installs BOTH ssh paths FIRST (operator key + allowlisted deploy key), so
a half-built box is still ssh-able and troubleshootable — the property
the old box lacked.

Secrets required (in addition to Steps 5/5b):

```
BOX_SSH_KEY        (deploy key PRIVATE half — workflow only)
DEPLOY_PUBKEY      (deploy key PUBLIC half — allowlist install)
OPERATOR_PUBKEY    (your interactive admin key — break-glass via SSH)
GOOGLE_SA_KEY      (base64 service-account JSON: compute.instanceAdmin.v1,
                    compute.networkUser, storage.objectViewer on houses-seed)
CF_TUNNEL_TOKEN    (Cloudflare Zero Trust tunnel token — the one dashboard value)
```

No age/rclone/OCI — the site is public, the seed is a private GCS object;
there was never an off-box encrypted backup (Phase 6 was never installed).
Seed failure = box with no data: box-bootstrap.sh logs "no seed" and the
box still boots; re-run seed-box.sh and re-provision.

Break-glass if the workflow itself is unusable: GCP console → serial
output → reimage → re-run `action=provision`.

## 6. Your first release (the whole loop)

1. Push a tag: `git tag v0.1.0 && git push origin v0.1.0` — the Release
   workflow deploys to the standby (green), snapshots the DB, starts green,
   runs the authenticated smoke checks, and reports.
2. **Eyeball the standby** at https://houses-smoke.blueumbrella.net —
   sign in, open a property, look at a commute. It is a full replica of prod
   (data from the snapshot); everything you do there writes only to the
   standby's copy.
3. When it looks right: GitHub → Actions → Release → Run workflow →
   action `switch`. Traffic moves to green; blue
   becomes the standby for next time.
4. Something wrong? Run workflow → action `rollback`. Blue (previous code)
   comes back with the pre-flip DB snapshot restored.

## 7. Nightly backups (do not skip — plan doc Phase 6)

The backup units in `docs/deployment-oracle-free-tier.md` Phase 6 are
unchanged: on-box snapshot + age-encrypted off-box push, 03:00 daily,
30 copies kept. The pre-flip snapshots from switch.sh are extra safety, not
a substitute.

---

## Gotchas (learned the hard way)

- **The standby writes to its own smoke DB** (`/opt/houses/<side>-smoke.db`)
  — that is the design. Never point the live unit's HOUSES_SQLITE_PATH at a
  smoke copy and vice versa; run-instance.sh derives it from ACTIVE, so
  don't hand-edit unit files to override it.
- **HOUSES_PORT in /etc/houses.env is ignored** (run-instance.sh sets it).
  Leave it out.
- **The LAN `.env` still contains sheet-era keys** (HOUSES_SHEET_ID,
  GOOGLE_SHEETS_SERVICE_ACCOUNT) — they crash pydantic at boot
  (extra_forbidden). Strip them when installing /etc/houses.env.
- **A1 capacity**: if the instance won't launch, retry over a day; a $4–6
  VPS with 4+ GB RAM is the fallback — the scripts don't care what the box
  is, only that Ubuntu + systemd + Caddy exist.
- **Rollback restores the pre-flip DB snapshot unconditionally** — anything
  written between flip and rollback is lost by design (deterministic,
  short window). If you need those writes, don't roll back; fix forward.
