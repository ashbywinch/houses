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
  personal key). Generate a dedicated key; in the box's
  `~ubuntu/.ssh/authorized_keys` add a `command=`-restricted entry:
  ```
  command="sudo /opt/houses/release.sh $1 2>/dev/null || true",no-pty,no-agent-forwarding,no-port-forwarding ssh-ed25519 AAA… deploy@houses
  ```
  plus a second entry for switch.sh, OR (simpler) allow the key to run any
  command but only as a dedicated user with a sudoers rule:
  ```
  housesdeploy ALL=(root) NOPASSWD: /opt/houses/release.sh, /opt/houses/switch.sh, /usr/bin/systemctl restart houses-*, /usr/bin/systemctl start houses-*, /usr/bin/systemctl stop houses-*
  ```
  (Pick the command-restricted key if you want least privilege; the sudoers
  list is the pragmatic middle. The key is a GitHub secret either way.)

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
