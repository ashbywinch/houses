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
- **VCN security list**: add an ingress rule **8765/tcp from 0.0.0.0/0**
  (the app port). Leave 9222 (Chrome) closed to the internet — it binds
  localhost only.
- SSH in: `ssh -i ~/.ssh/oracle.pem ubuntu@<public-ip>`.

## Phase 2 — box setup

```bash
# deps
sudo apt update && sudo apt install -y python3-venv unzip curl ca-certificates \
  fonts-liberation libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libxkbcommon0 \
  libxcomposite1 libxdamage1 libgbm1 libasound2

# uv (the repo's package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Chrome for the scraper (arm64 deb)
curl -Lo /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_arm64.deb
sudo dpkg -i /tmp/chrome.deb || sudo apt -f install -y
```

- Launch headless Chrome with remote debugging as a **systemd service**
  (`houses-chrome.service`):
  `google-chrome --headless=new --no-sandbox --disable-dev-shm-usage --remote-debugging-port=9222 --user-data-dir=/var/lib/houses-chrome about:blank`
  The scraper connects to it via `rightmove_chrome_port` (default 9222 ✓).
- Verify: `curl -s localhost:9222/json/version` returns the browser version.

## Phase 3 — app deploy

- Copy the repo (fresh clone is cleanest — the working tree has uncommitted
  session work that stays local):

  ```bash
  git clone https://github.com/ashbywinch/houses.git /opt/houses && cd /opt/houses
  uv sync --all-extras
  ```

- Copy the live data and secrets (from the LAN machine, **never deleting the
  source**):

  ```bash
  # consistent snapshot of the DB while the app runs
  sqlite3 data/houses.db ".backup '/tmp/houses-backup.db'"
  scp /tmp/houses-backup.db ubuntu@<ip>:/opt/houses/data/houses.db
  scp -r data/api_cache ubuntu@<ip>:/opt/houses/data/
  scp .env ubuntu@<ip>:/opt/houses/.env
  ```

- Frontend: the built `dist/` is committed in the repo — no build step
  needed; `run-prod` serves it from FastAPI on 8765.

## Phase 4 — systemd service

`houses.service` (env from `.env`, WorkingDirectory `/opt/houses`,
`Restart=always`):

```ini
EnvironmentFile=/opt/houses/.env
ExecStart=/home/ubuntu/.local/bin/uv run python -c "
  import uvicorn
  from houses.config import settings
  from houses.server import app
  from fastapi.staticfiles import StaticFiles
  from pathlib import Path
  build = Path('/opt/houses/houses/frontend/dist')
  if build.exists(): app.mount('/', StaticFiles(directory=str(build), html=True), name='frontend')
  uvicorn.run(app, host='0.0.0.0', port=settings.port, reload=False)"
```

This is exactly the `make run-prod` shape — one process, background DAG
scheduler + WebSocket broadcaster included, no dev reload.

## Phase 5 — OAuth + no-domain launch

- In `/opt/houses/.env` set:
  `HOUSES_PUBLIC_URL=http://<public-ip>.sslip.io:8765`
  `HOUSES_FRONTEND_URL=http://<public-ip>.sslip.io:8765`
  (same hostname — cookie host matches the callback).
- Google Cloud Console → OAuth client → authorized redirect URIs: **add
  `http://<public-ip>.sslip.io:8765/api/auth/callback`** (keep the LAN one
  while both run). Consent screen: add your brother's Google account as a
  **test user** if it's still in Testing mode.
- Restart `houses.service`, then verify from your phone on **cellular**: page
  loads → Google sign-in completes → a property detail opens → the WebSocket
  stays connected.

## Phase 6 — safety net (the part that matters)

- **Nightly backup timer** (`houses-backup.timer` + service):
  `sqlite3 data/houses.db ".backup '/var/backups/houses-$(date +%F).db'"` plus
  a copy of `.env`, keep N days. Optionally `rclone` to object storage later —
  the DB is the only copy of the family's finances; treat backups like the
  repo rules treat the DB.
- Monitoring: `journalctl -u houses -f` for errors; systemd restart policy
  handles crashes.
- The `HOUSES_SHEET_ID` / service-account entries become inert once the
  sheets code is removed — harmless until then.

## Phase 7 — domain cutover (later, ~15 min)

A record `houses.<yourdomain> → <public-ip>` (or Cloudflare proxy — enable
WebSockets; origin stays plain http on 8765, "Full (strict)" TLS). Update the
two env vars to `https://houses.<yourdomain>`, add that callback URI to
Google, restart. Nothing else changes.

---

## Risks / honest caveats

- **A1 capacity** is the one real blocker — if unavailable, a $4 VPS is the
  pragmatic fallback.
- **First cutover copies the DB, not syncs it** — anything saved on the LAN
  app after the copy is lost to the VM (and vice versa). Do it in the evening
  window and stop the LAN app during the copy.
- The app has **no Dockerfile** — this plan deploys it directly (uv +
  systemd). Containerizing later is optional and doesn't change the shape.
