# Deployment Plan — houses on Oracle Cloud Free Tier

Host the houses app (FastAPI + Vue + SQLite + Rightmove scraper) on an Oracle
Cloud **Free Tier ARM (Ampere A1)** instance so the family can reach it from
anywhere without LAN/DNS tricks. The Chrome-based scraper stays in
production, which is why the free ARM shape (24 GB RAM) is the host — no free
"services" tier (Render sleeps, Fly is 256 MB, Railway isn't free) fits.

---

## Phase 0 — decisions before provisioning

1. **Region with A1 capacity.** The ARM Ampere A1 (4 OCPU / 24 GB) is the
   only free shape that runs Chrome comfortably. A1 capacity is frequently
   "out of capacity" at signup — pick a region with availability (commonly US
   regions, sometimes EU), and if the launch keeps failing, retry over a day
   or fall back to a $4–6 VPS (the free 2×AMD micro at 1 GB is too small with
   Chrome — don't fight that).
2. **Reserved Public IP.** Allocate a *Reserved* public IP at instance
   creation so the address survives reboots — the sslip.io hostname and the
   Google OAuth redirect URI must stay stable.
3. **Cutover moment.** The live `data/houses.db` is the single source of
   truth. Pick a window (e.g. evening) when the family isn't using the app;
   the LAN instance stops being authoritative after the copy. The LAN dev
   instance can keep running for development afterward — the two copies
   diverge from then on.

## Phase 1 — Oracle provisioning (~20 min)

- Create the tenancy, then **Compute → Instances → Create** with:
  - Image: **Ubuntu 24.04** (arm64), Shape: **VM.Standard.A1.Flex**,
    4 OCPU / 24 GB RAM, boot volume ~150–200 GB.
  - **Reserved public IP**, SSH key pair (download the `.pem`, `chmod 600`).
- **VCN security list**: add an ingress rule **8765/tcp from the family's
  egress IPs only** — not `0.0.0.0/0`. The interim phase serves financial
  data over plain HTTP (see Phase 5), so keep the exposure to known
  networks from the start; widen it only if you must, and never leave it
  open after the Phase 7 TLS cutover. Leave 9222 (Chrome) closed to the
  internet — it binds localhost only.
- SSH in: `ssh -i ~/.ssh/oracle.pem ubuntu@<public-ip>`.

## Phase 2 — box setup

```bash
# deps
sudo apt update && sudo apt install -y python3-venv unzip curl ca-certificates sqlite3 rsync nodejs npm \
  fonts-liberation libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libxkbcommon0 \
  libxcomposite1 libxdamage1 libgbm1 libasound2
# Ubuntu 24.04 ships node 22 — satisfies vite 8's requirement
# (^20.19 || >=22.12); recheck if the frontend bumps vite.

# uv (the repo's package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Chrome for the scraper — Google ships an arm64 Linux .deb (the URL was
# verified resolving with a binary payload at plan-write time, 2026-08).
# The download is sanity-checked anyway, so a failed fetch can't silently
# leave the scraper browserless. If the deb is unavailable, fall back to
# Ubuntu's chromium and point houses-chrome.service's ExecStart at it:
#   sudo apt install -y chromium-browser   # then use /usr/bin/chromium-browser
curl -Lo /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_arm64.deb
if file /tmp/chrome.deb | grep -q "Debian binary package"; then
  sudo dpkg -i /tmp/chrome.deb || sudo apt -f install -y
else
  echo "chrome deb unavailable — installing chromium instead"
  sudo apt install -y chromium-browser
fi
# if the fallback ran, point houses-chrome.service's ExecStart at the
# chromium binary (/usr/bin/chromium-browser)
```

- Launch headless Chrome with remote debugging as a **systemd service**
  (`houses-chrome.service`) — as an **unprivileged user** (a root Chrome
  with `--no-sandbox` would be a host compromise if the scraper ever
  loads a malicious page; Ubuntu's user-namespace kernel config lets a
  normal user run Chrome with its sandbox on):

  ```ini
  [Service]
  User=ubuntu
  ExecStart=/usr/bin/google-chrome --headless=new --disable-dev-shm-usage --remote-debugging-port=9222 --user-data-dir=/var/lib/houses-chrome about:blank
  Restart=always
  RestartSec=5
  [Install]
  WantedBy=multi-user.target
  ```

  (Give the user the data dir: `sudo mkdir -p /var/lib/houses-chrome && sudo chown ubuntu:ubuntu /var/lib/houses-chrome`.)
  Verify: `curl -s localhost:9222/json/version` returns the browser version.

## Phase 3 — app deploy

- Copy the repo (fresh clone is cleanest — the working tree has uncommitted
  session work that stays local; `/opt` is root-owned, so claim it first):

  ```bash
  sudo mkdir -p /opt/houses && sudo chown ubuntu:ubuntu /opt/houses
  git clone https://github.com/ashbywinch/houses.git /opt/houses && cd /opt/houses
  uv sync --all-extras
  ```

- Copy the live data and secrets (from the LAN machine, **never deleting the
  source**). **Stop the LAN app first** (`make stop` on the LAN machine)
  so nothing writes to the DB after the snapshot:

  > **Secrets — environment only.** `docs/coding-standards.md` says secrets
  > come from the environment, never files. The single path is the
  > root-only `/etc/houses.env` above (chmod 600, outside the repo tree);
  > nothing secret is copied into `/opt/houses` or committed. The backup
  > timer retains it with 600 perms (Phase 6) — that is the one accepted
  > file copy, root-readable only.

  ```bash
  # the LAN app is stopped (make stop, above) — .backup of a live DB is
  # consistent, but any write after the snapshot never reaches the VM
  sqlite3 data/houses.db ".backup '/tmp/houses-backup.db'"
  ssh ubuntu@<ip> "mkdir -p /opt/houses/data"   # a fresh clone has no data/
  scp /tmp/houses-backup.db ubuntu@<ip>:/opt/houses/data/houses.db
  # every other runtime file — the disk API cache, the commute toolchain
  # outputs, the council-tax/rail/fare/Ofsted CSVs, bus/parking data —
  # except the live DB and its WAL files (they come from the snapshot)
  rsync -a --exclude 'houses.db*' data/ ubuntu@<ip>:/opt/houses/data/
  # Secrets: install the LAN .env as a ROOT-ONLY /etc/houses.env —
  # strip any quotes/export/$ first, because systemd's EnvironmentFile
  # parser is strict KEY=VALUE (the app's own pydantic would handle
  # them, but this file feeds the unit). Nothing secret stays in the
  # repo tree.
  scp .env ubuntu@<ip>:/tmp/houses.env
  # `;` not `&&` — the /tmp copy is removed even if install fails, so a
  # failed cutover never leaves secrets in /tmp; the verification then
  # makes a failed install visible
  ssh ubuntu@<ip> "sudo install -o root -g root -m 600 /tmp/houses.env /etc/houses.env; rm -f /tmp/houses.env"
  ssh ubuntu@<ip> "sudo test -r /etc/houses.env && echo 'secrets installed'"
  # The VM listens on settings.port — the env must agree with the VCN
  # ingress rule (8765). A dev .env that sets HOUSES_PORT elsewhere
  # would put the app behind the firewall before Phase 5 even starts.
  ssh ubuntu@<ip> "grep -q '^HOUSES_PORT=8765' /etc/houses.env && echo 'port 8765 OK' || echo 'HOUSES_PORT mismatch or unset — align env with the firewall rule'"
  ```

- Frontend: the built `dist/` is committed in the repo — no build step
  needed; `run-prod` serves it from FastAPI on 8765.

## Phase 4 — systemd service

Create the launcher script (systemd `ExecStart` must be a single line).
It delegates to the repo's own `make run-prod` target
([Makefile](https://github.com/ashbywinch/houses/blob/main/Makefile#L140-L145))
so there is ONE source of truth for the launch command — no inline copy
to drift:

```bash
# /opt/houses/run_prod.sh
cat > /opt/houses/run_prod.sh <<'EOF'
#!/bin/sh
cd /opt/houses
# systemd's default PATH lacks ~/.local/bin (uv) — make's UV fallback
# handles it, but be explicit so make/npm resolve identically to a shell
export PATH="$HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
exec make run-prod
EOF
chmod +x /opt/houses/run_prod.sh
```

Two things `make run-prod` needs from the environment on a server:
`make run-prod` binds `settings.host` (127.0.0.1 by default) and
re-runs `setup` + `frontend-build` (a few seconds on a warmed cache),
so the box's env must set `HOUSES_HOST=0.0.0.0` — add it to
`/opt/houses/.env` (or the service `Environment=`) alongside the
Phase 3 values, and expect a short boot before the port answers.

`houses.service` (env from `.env`, WorkingDirectory `/opt/houses`,
`Restart=always`):

```ini
# /etc/systemd/system/houses.service
[Unit]
Description=Houses app (FastAPI + Vue + scraper)
After=network-online.target
Wants=network-online.target

[Service]
User=ubuntu
WorkingDirectory=/opt/houses
# Secrets come from a root-only env file (chmod 600 — a systemd unit
# file is world-readable, so never put secrets in Environment= lines
# here). /etc/houses.env must be STRICT KEY=VALUE: no quotes, no
# export, no $ references (systemd's parser is not a dotenv parser).
# The app's pydantic reads process env first, then ./.env, so these
# win over anything in the repo.
EnvironmentFile=/etc/houses.env
ExecStart=/bin/sh /opt/houses/run_prod.sh
Restart=always

[Install]
WantedBy=multi-user.target
```

## Phase 5 — OAuth + no-domain launch

Google rejects plain-`http://` redirect URIs for **public** hosts, so the
web-flow sign-in needs HTTPS, which the no-domain interim does not have.
Two workable paths:

- **Interim (no domain): use the device flow.** The app ships one
  (`/api/auth/device` — the same path `tools/capture_dom.py --login`
  uses). On the box: `HOUSES_PUBLIC_URL` / `HOUSES_FRONTEND_URL` are
  irrelevant to it, but keep them set to
  `http://<public-ip>.sslip.io:8765` so the app's links are consistent.
  Run `make login` once per new user: it prints a Google approval URL —
  the person opens it on any device and approves; the box mints the
  session. Costs a code + approval dance per user, but works with no
  HTTPS and no domain. (The LAN web-flow callback stays configured for
  the dev machine.)
- **Full web-flow sign-in arrives with the domain cutover (Phase 7)** —
  register `https://houses.<yourdomain>/api/auth/callback` there and the
  normal "Sign in with Google" button works.

Restart `houses.service`, then verify from a whitelisted network (your
home WiFi — or add the phone's **cellular egress IP** to the security
list temporarily, since a carrier NAT address is not on the family-IP
allowlist): page loads → sign-in completes (device flow for now) → a
property detail opens → the WebSocket stays connected.

> If you have the domain from day one, skip the interim: go straight to
> Phase 7, register the `https` callback, and use the web flow throughout.

> **Security during the interim:** the app carries family financial data
> over plain HTTP on a public IP until Phase 7. Minimise the window —
> restrict the OCI security-list ingress for 8765/tcp to the IPs the
> family actually connects from (your home connection, your brother's),
> not `0.0.0.0/0`, and treat Phase 7 as the real fix (HTTPS terminates
> at Cloudflare). If you want HTTPS from day one without a domain, put
> Caddy in front of the box and use a Cloudflare tunnel to a domain you
> already own — either removes the plain-HTTP exposure entirely.

## Phase 6 — safety net (the part that matters)

- **Nightly backup timer** (`houses-backup.service` + `.timer`): systemd
  does not expand `$(date …)` in `ExecStart`, so wrap it in a shell:

  ```ini
  # /etc/systemd/system/houses-backup.service
  [Unit]
  Description=Nightly houses backup

  [Service]
  Type=oneshot
  ExecStart=/bin/sh -c 'ts=$(date +%F); sqlite3 /opt/houses/data/houses.db ".backup /var/backups/houses-${ts}.db" && chmod 600 /var/backups/houses-${ts}.db && cp /etc/houses.env /var/backups/houses-${ts}.env && chmod 600 /var/backups/houses-${ts}.env && find /var/backups -name "houses-*.db" -mtime +30 -delete && find /var/backups -name "houses-*.env" -mtime +30 -delete'

  # /etc/systemd/system/houses-backup.timer
  [Unit]
  Description=Run the houses backup nightly

  [Timer]
  OnCalendar=*-*-* 03:00:00
  Persistent=true

  [Install]
  WantedBy=timers.target
  ```

  Enable with `sudo systemctl enable --now houses-backup.timer`.

  **Make the backup off-box from day one** — on-box `/var/backups` dies
  with the instance, and the DB is the only copy of the family's
  finances. Add a second step to the timer that pushes the newest
  snapshot somewhere else: a **private, authenticated** OCI Object
  Storage bucket via `rclone` with access keys (never a
  pre-authenticated request URL — anyone holding the URL can read it),
  a second VM, or the LAN machine
  (`rsync -a /var/backups/ user@lan-host:/backups/houses/`).
  The `.env` snapshot is secrets — **encrypt it before any off-box
  transfer** (`age -e -r <key> /var/backups/houses-${ts}.env`) so the
  copy that leaves the box is ciphertext, not the Google client
  secrets.
  Without an off-box copy, a single-instance loss (termination, disk
  failure) is a total loss.
- Monitoring: `journalctl -u houses -f` for errors; systemd restart policy
  handles crashes.
- The `HOUSES_SHEET_ID` / service-account entries become inert once the
  sheets code is removed — harmless until then.

## Phase 7 — domain cutover (later, ~15 min)

A record `houses.<yourdomain> → <public-ip>`. With Cloudflare proxy in
front, **the origin port must be one Cloudflare forwards to** — its
supported plain-HTTP origin ports are 80/8080/8880/2052/2082/2086/2095
(8765 is not among them). Two clean options:

- **Cloudflare Tunnel** (`cloudflared tunnel route dns houses …`): reaches
  any origin port, so 8765 keeps working untouched.
- **Proxied record**: move the app to a supported port first — set
  `HOUSES_PORT=8080` in `/opt/houses/.env` (the launcher already runs
  `uvicorn … port=settings.port`, so no code change is needed — just
  restart `houses.service`), update the OCI security list for 8080 — and
  use **Flexible** TLS with a plain-HTTP origin ("Full (strict)" fails
  with 526 because it requires a certificate on the origin itself).

Either way, at cutover **restrict the OCI security list for the app port
to Cloudflare's published IP ranges** (`https://www.cloudflare.com/ips/`)
instead of `0.0.0.0/0` — otherwise the origin IP stays directly
reachable over plain HTTP and the TLS you just added is bypassable.

Register `https://houses.<yourdomain>/api/auth/callback` in Google, update
the two env vars to `https://houses.<yourdomain>`, restart. This is also
the step that restores the normal "Sign in with Google" button (the
Phase 5 device flow can be retired). Nothing else changes.

---

## Risks / honest caveats

- **A1 capacity** is the one real blocker — if unavailable, a $4 VPS is the
  pragmatic fallback.
- **First cutover copies the DB, not syncs it** — anything saved on the LAN
  app after the copy is lost to the VM (and vice versa). Do it in the evening
  window and stop the LAN app during the copy.
- The app has **no Dockerfile** — this plan deploys it directly (uv +
  systemd). Containerizing later is optional and doesn't change the shape.
