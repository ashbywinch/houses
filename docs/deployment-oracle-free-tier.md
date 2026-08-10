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
sudo apt update && sudo apt install -y python3-venv unzip curl ca-certificates sqlite3 rsync age rclone file nodejs npm git make \
  fonts-liberation libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libxkbcommon0 \
  libxcomposite1 libxdamage1 libgbm1 libasound2
# Ubuntu 24.04's nodejs is 22 via noble-updates (checked against a 24.04
# apt cache: 22.22.1) — satisfies vite 8's ^20.19 || >=22.12. If your
# image's nodejs still resolves to 18 (pre-SRU), install 22 via NodeSource.

# uv (the repo's package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# cloudflared for the HTTPS tunnel (Phase 5 primary path) — not in
# Ubuntu's apt; Cloudflare publishes a per-arch .deb on GitHub
# releases. Sanity-checked like the Chrome download (file(1) + dpkg
# fails loudly on a bad payload).
curl -fL --retry 3 -o /tmp/cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb || { echo "cloudflared download failed"; exit 1; }
file /tmp/cloudflared.deb | grep -q "Debian binary package" || { echo "cloudflared.deb is not a valid package"; exit 1; }
sudo dpkg -i /tmp/cloudflared.deb

# Chrome for the scraper — Google ships an arm64 Linux .deb (the URL was
# verified resolving with a binary payload at plan-write time, 2026-08).
# The download is sanity-checked anyway, so a failed fetch can't silently
# leave the scraper browserless. If the deb is unavailable, fall back to
# Ubuntu's chromium and point houses-chrome.service's ExecStart at it:
#   sudo apt install -y chromium-browser   # then use /usr/bin/chromium-browser
curl -Lo /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_arm64.deb
if file /tmp/chrome.deb | grep -q "Debian binary package"; then
  sudo dpkg -i /tmp/chrome.deb || sudo apt -f install -y
  CHROME_BIN=/usr/bin/google-chrome
else
  echo "chrome deb unavailable — installing chromium instead"
  # Ubuntu 24.04's apt chromium-browser is a snap transitional package —
  # the first launch pulls the snap; verify it actually starts headless
  sudo apt install -y chromium-browser
  CHROME_BIN=/usr/bin/chromium-browser
fi
$CHROME_BIN --headless=new --version || { echo "$CHROME_BIN does not launch headless — fix before continuing"; exit 1; }

# generate the unit with the binary that was actually installed — no
# manual paste, so a fallback install can't leave the unit pointing at
# the wrong browser
sudo tee /etc/systemd/system/houses-chrome.service >/dev/null <<EOF
[Unit]
Description=Headless browser for the Rightmove scraper

[Service]
User=ubuntu
ExecStart=$CHROME_BIN --headless=new --disable-dev-shm-usage --remote-debugging-port=9222 --user-data-dir=/var/lib/houses-chrome about:blank
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo mkdir -p /var/lib/houses-chrome && sudo chown ubuntu:ubuntu /var/lib/houses-chrome
sudo systemctl daemon-reload && sudo systemctl enable --now houses-chrome.service
for i in $(seq 1 15); do curl -fsS --max-time 3 localhost:9222/json/version >/dev/null 2>&1 && break; sleep 1; done
curl -fsS --max-time 3 localhost:9222/json/version >/dev/null 2>&1 || { echo "Chrome CDP endpoint not answering on :9222 — check houses-chrome.service"; exit 1; }
```

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
  set -e   # abort the cutover if any copy step fails
  trap 'rm -f /tmp/houses-backup.db' EXIT   # clean up even on failure
  # the LAN app is stopped (make stop, above) — .backup of a live DB is
  # consistent, but any write after the snapshot never reaches the VM.
  # The snapshot is the family's financial data: private perms, and it
  # is removed on every exit path (never left in world-readable /tmp).
  umask 077
  sqlite3 data/houses.db ".backup '/tmp/houses-backup.db'"
  ssh ubuntu@<ip> "mkdir -p /opt/houses/data"   # a fresh clone has no data/
  cat /tmp/houses-backup.db | ssh ubuntu@<ip> "umask 077; cat > /opt/houses/data/houses.db && chmod 600 /opt/houses/data/houses.db && sqlite3 /opt/houses/data/houses.db 'PRAGMA integrity_check;' | grep -q '^ok$'"
  # confirm the copy is byte-identical to the snapshot
  test "$(sha256sum /tmp/houses-backup.db | cut -d' ' -f1)" = "$(ssh ubuntu@<ip> sha256sum /opt/houses/data/houses.db | cut -d' ' -f1)"
  rm -f /tmp/houses-backup.db
  # every other runtime file — the disk API cache, the commute toolchain
  # outputs, the council-tax/rail/fare/Ofsted CSVs, bus/parking data —
  # except the live DB and its WAL files (they come from the snapshot)
  rsync -a --exclude 'houses.db*' data/ ubuntu@<ip>:/opt/houses/data/
  # Secrets: install the LAN .env as a ROOT-ONLY /etc/houses.env —
  # sanitized to strict KEY=VALUE first, because systemd's
  # EnvironmentFile parser strips no quotes and expands nothing (the
  # app's own pydantic would handle the original, but this file feeds
  # the unit). Process env beats the repo .env, so HOUSES_HOST /
  # HOUSES_PORT / the public URLs belong here too, not in
  # /opt/houses/.env. Nothing secret stays in the repo tree.
  # Stream the LAN .env straight into the conversion — no /tmp copy, so
  # an interrupted cutover can never leave a world-readable plaintext
  # secrets file behind. Values are written PLAIN KEY=VALUE (systemd's
  # EnvironmentFile keeps everything after '=' — no quotes needed, and
  # a quoted value would be mangled); values containing a literal
  # newline / backslash / quote are rejected (they would split or
  # re-quote into a malformed line — systemd's EnvironmentFile parser
  # differs from dotenv's, so round-tripping them is not safe).
  cat .env | ssh ubuntu@<ip> "set -o pipefail && sudo /opt/houses/.venv/bin/python -c \"import sys; from dotenv import dotenv_values; vals = dotenv_values(stream=sys.stdin, interpolate=False); bad = [k for k, v in vals.items() if v is not None and any(c in v for c in (chr(10), chr(92), chr(34), chr(39)))]; assert not bad, 'unsafe character (newline/backslash/quote) in value: ' + ', '.join(bad); [print(f'{k}={v}') for k, v in vals.items() if v is not None]\" | sudo install -o root -g root -m 600 /dev/stdin /etc/houses.env"
  # values are written PLAIN KEY=VALUE — no quotes (the doc's own rule,
  # and systemd's EnvironmentFile keeps everything after the '='), so
  # nothing mangled reaches the app; interpolate=False prevents a \$VAR
  # in any value from being silently rewritten mid-cutover.
  cat .env | ssh ubuntu@<ip> "sudo /opt/houses/.venv/bin/python -c \"import sys; from dotenv import dotenv_values; src = dotenv_values(stream=sys.stdin, interpolate=False); got = dotenv_values('/etc/houses.env', interpolate=False); keys = ('HOUSES_SESSION_SECRET', 'HOUSES_GOOGLE_WEB_CLIENT_ID', 'HOUSES_GOOGLE_WEB_CLIENT_SECRET', 'HOUSES_GOOGLE_DEVICE_CLIENT_ID', 'HOUSES_GOOGLE_DEVICE_CLIENT_SECRET', 'HOUSES_SHEET_ID'); ok = all(src.get(k) == got.get(k) for k in keys); print('all critical values match' if ok else 'value mismatch'); exit(0 if ok else 1)\""
  # force the deployment host/port — the LAN .env's values (or their
  # absence) would otherwise leave the app bound to loopback and the
  # firewall would never see a listener
  ssh ubuntu@<ip> "sudo sh -c 'grep -q \"^HOUSES_HOST=\" /etc/houses.env && sed -i \"s/^HOUSES_HOST=.*/HOUSES_HOST=0.0.0.0/\" /etc/houses.env || echo HOUSES_HOST=0.0.0.0 >> /etc/houses.env'"
  ssh ubuntu@<ip> "sudo sh -c 'grep -q \"^HOUSES_PORT=\" /etc/houses.env && sed -i \"s/^HOUSES_PORT=.*/HOUSES_PORT=8765/\" /etc/houses.env || echo HOUSES_PORT=8765 >> /etc/houses.env'"
  # the LAN .env's public URLs point at the dev host — force them to the
  # VM hostname or every link the app generates goes back to the LAN.
  # The public IP is defined ONCE here; the sslip.io default is what the
  # no-domain fallback needs (Phase 5's tunnel path replaces these with
  # https://houses.<yourdomain>).
  PUBIP=<public-ip>
  ssh ubuntu@<ip> "sudo sh -c 'grep -q \"^HOUSES_PUBLIC_URL=\" /etc/houses.env && sed -i \"s|^HOUSES_PUBLIC_URL=.*|HOUSES_PUBLIC_URL=http://${PUBIP}.sslip.io:8765|\" /etc/houses.env || echo HOUSES_PUBLIC_URL=http://${PUBIP}.sslip.io:8765 >> /etc/houses.env'"
  ssh ubuntu@<ip> "sudo sh -c 'grep -q \"^HOUSES_FRONTEND_URL=\" /etc/houses.env && sed -i \"s|^HOUSES_FRONTEND_URL=.*|HOUSES_FRONTEND_URL=http://${PUBIP}.sslip.io:8765|\" /etc/houses.env || echo HOUSES_FRONTEND_URL=http://${PUBIP}.sslip.io:8765 >> /etc/houses.env'"
  # the <public-ip> placeholder must be substituted — a literal one
  # would make every app link point at a hostname that resolves nowhere
  ssh ubuntu@<ip> "sudo grep -q '<public-ip>' /etc/houses.env && { echo 'substitute <public-ip> with the VM address first'; exit 1; } || echo 'URLs OK'"
  # fail loudly if the conversion dropped or blanked anything — the
  # critical keys must be present with a NON-EMPTY value (the file is
  # root-only, so the check needs sudo)
  ssh ubuntu@<ip> "for k in HOUSES_SESSION_SECRET HOUSES_GOOGLE_WEB_CLIENT_ID HOUSES_GOOGLE_WEB_CLIENT_SECRET HOUSES_GOOGLE_DEVICE_CLIENT_ID HOUSES_GOOGLE_DEVICE_CLIENT_SECRET HOUSES_SHEET_ID; do sudo grep -q \"^\$k=.\" /etc/houses.env || { echo \"missing or empty \$k in /etc/houses.env\"; exit 1; }; done && echo 'secrets intact'"
  # `;` not `&&` — the /tmp copy is removed even on failure, so a failed
  # cutover never leaves secrets in /tmp; the verification makes a
  # failed install visible
  ssh ubuntu@<ip> "sudo test -r /etc/houses.env && sudo grep -q '^HOUSES_PORT=8765' /etc/houses.env && echo 'secrets installed, port 8765 OK' || { echo 'check /etc/houses.env (missing or HOUSES_PORT mismatch with the firewall rule)'; exit 1; }"
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
# wait for the scraper browser's CDP endpoint before serving (the app
# can start either way, but property adds need it live) — and fail
# loudly if it never comes up
for i in $(seq 1 30); do curl -fsS --max-time 3 localhost:9222/json/version >/dev/null 2>&1 && break; sleep 1; done
curl -fsS --max-time 3 localhost:9222/json/version >/dev/null 2>&1 || { echo "scraper browser not reachable on :9222"; exit 1; }
exec make run-prod
EOF
chmod +x /opt/houses/run_prod.sh
```

Two things `make run-prod` needs from the environment on a server:
`make run-prod` binds `settings.host` (127.0.0.1 by default) and
re-runs `setup` + `frontend-build` (a few seconds on a warmed cache).
Put `HOUSES_HOST=0.0.0.0` in `/etc/houses.env` (the unit's
`EnvironmentFile` — process env wins over the repo `.env`), start and
enable the app: `sudo systemctl daemon-reload && sudo systemctl enable
--now houses.service`, and after start verify the listener answers
externally: `curl -s --max-time 10 -o /dev/null -w 'listener answered with HTTP %{http_code}\n' http://<public-ip>:8765/api/auth/me || { echo 'app unreachable externally — check HOUSES_HOST and the security list'; exit 1; }` and
`ssh ubuntu@<ip> "ss -tlnp | grep -E '(0\.0\.0\.0|\[::\]):8765' || { echo 'houses bound to loopback — HOUSES_HOST=0.0.0.0 not applied'; exit 1; }"`. Expect a short boot before
the port answers.

**Negative check — the ingress rule must be verified, not assumed.**
The positive curl above succeeds whether the security list is restricted
to the family's egress IPs or mistakenly left `0.0.0.0/0`, and a
wide-open rule serves the family's financial data over sniffable HTTP
to anyone who scans the port. Before the app is used, confirm from a
network that is NOT allowlisted (e.g. the phone on cellular) that
`curl -s --max-time 5 http://<public-ip>:8765/` is refused or times
out; if it answers, fix the VCN security-list ingress for 8765/tcp
before proceeding.

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

Google rejects plain-`http://` redirect URIs for **public** hosts, and
the app carries family financial data — plain HTTP on a public IP is
sniffable regardless of allowlisting. **HTTPS is required from the
start.** Since you already own a domain, the primary path is:

- **Primary: Cloudflare Tunnel to your existing domain — HTTPS from day
  one.** `cloudflared tunnel create houses`, route
  `houses.<yourdomain>` to it, and let the tunnel publish the origin
  port (it reaches any port, so 8765 stays). The origin speaks plain
  HTTP **inside** the tunnel, never on the public internet. Register
  `https://houses.<yourdomain>/api/auth/callback` in Google and the
  normal "Sign in with Google" button works immediately — no device
  flow, no interim. Set `HOUSES_PUBLIC_URL` /
  `HOUSES_FRONTEND_URL` to `https://houses.<yourdomain>`.
- **Fallback (accepting the risk): no-domain sslip.io over plain HTTP
  with the device flow.** Only if you accept on-path eavesdropping of
  the family's financial data and session cookies on a public IP —
  restrict the security list to trusted networks, treat it as strictly
  temporary, and migrate to the tunnel before anyone uses the app from
  an untrusted network. The app's device flow (`make login` on the box)
  works with no HTTPS and no redirect URI; the "Sign in with Google"
  button stays dead until the tunnel/domain lands.

Restart `houses.service`, then verify from a whitelisted network (your
home WiFi — or add the phone's **cellular egress IP** to the security
list temporarily, since a carrier NAT address is not on the family-IP
allowlist): page loads → sign-in completes → a property detail opens →
the WebSocket stays connected.

## Phase 6 — safety net (the part that matters)

- **Nightly backup** — two decoupled units: the on-box snapshot (a
  failure stops it loudly; the `&&` chain is deliberate) and a separate
  off-box push that runs after it, so a push problem never blocks the
  snapshot and vice versa. Trim keeps the newest 30 copies by COUNT
  (age-based trimming silently shrinks history after a gap):

  ```ini
  # /etc/systemd/system/houses-backup.service
  [Unit]
  Description=Nightly houses backup (on-box snapshot)
  Wants=houses-backup-push.service

  [Service]
  Type=oneshot
  ExecStart=/bin/sh -c 'ts=$(date +%%F-%%H%%M%%S); sqlite3 /opt/houses/data/houses.db ".backup /var/backups/houses-${ts}.db" && chmod 600 /var/backups/houses-${ts}.db && cp /etc/houses.env /var/backups/houses-${ts}.env && chmod 600 /var/backups/houses-${ts}.env && echo ${ts} > /var/backups/last-backup-ts && ls -1t /var/backups/houses-*.db | tail -n +31 | xargs -r rm && ls -1t /var/backups/houses-*.env | tail -n +31 | xargs -r rm'

  [Install]
  WantedBy=timers.target

  # /etc/systemd/system/houses-backup-push.service  (off-box push)
  [Unit]
  Description=Push the houses backup off-box
  After=houses-backup.service

  [Service]
  Type=oneshot
  # The push requires the snapshot produced by TONIGHT'S run: the
  # backup unit writes /var/backups/last-backup-ts only on success, and
  # a failed snapshot must not silently re-push yesterday's stale copy
  # (Wants= does not gate on the dependee's exit status). Remote
  # retention: the lsl verifies the landing, then rclone deletes
  # ciphertexts older than 31 days so the bucket does not grow forever.
  # Complete example (rclone remote "houses:" configured once via
  # `rclone config` with an authenticated S3-compatible/R2 bucket; the
  # age recipient is the operator's public key).
  ExecStart=/bin/sh -c 'ts=$(cat /var/backups/last-backup-ts); newest=/var/backups/houses-${ts}.db; envfile=${newest%%.db}.env; [ "${ts%%-*}" = "$(date +%%F)" ] || { echo "no snapshot from tonight (last: ${ts}) — backup failed"; exit 1; }; [ -f "$newest" ] || { echo "snapshot ${ts} missing"; exit 1; }; age -e -r age1<recipient> -o "${newest}.age" "$newest" && age -e -r age1<recipient> -o "${envfile}.age" "$envfile" && rclone copyto "${newest}.age" houses:backups/ && rclone copyto "${envfile}.age" houses:backups/ && rclone lsl houses:backups/ | grep -q "$(basename ${newest}).age" && rclone delete houses:backups/ --min-age 31d && ls -1t /var/backups/houses-*.db.age | tail -n +31 | xargs -r rm && ls -1t /var/backups/houses-*.env.age | tail -n +31 | xargs -r rm'

  # /etc/systemd/system/houses-backup.timer
  [Unit]
  Description=Run the houses backup nightly

  [Timer]
  OnCalendar=*-*-* 03:00:00
  Persistent=true
  Unit=houses-backup.service

  [Install]
  WantedBy=timers.target
  ```

  Enable with `sudo systemctl enable --now houses-backup.timer`.

  **Restore runbook** — drill this once before you need it; a backup that
  has never been restored is unverified:
  1. Bring up a fresh box and complete Phases 3–4 first (repo clone,
     `uv sync`, the `houses` / `houses-chrome` units, and
     `/etc/houses.env`) — a Phases 1–2 box has no `houses.service` yet —
     then stop its app: `sudo systemctl stop houses`.
  2. Restore the DB with absolute paths, in one block (relative paths in
     the wrong working directory would silently write the DB elsewhere);
     do NOT start the app yet:
     ```bash
     sudo systemctl stop houses
     rm -f /opt/houses/data/houses.db-wal /opt/houses/data/houses.db-shm
     umask 077
     age -d -i <key> /path/to/houses-<ts>.db.age > /opt/houses/data/houses.db
     sudo chmod 600 /opt/houses/data/houses.db
     sudo chown ubuntu:ubuntu /opt/houses/data/houses.db
     ```
  3. Restore `/etc/houses.env` from the backup (or re-run Phase 3's
     secrets step) — the environment must be in place BEFORE the first
     start, or the app serves (or restart-loops) with the wrong config
     and the later start is a no-op.
  4. Start and verify: `sudo systemctl restart houses`, then confirm a
     property detail loads and the WebSocket connects.

  Without an off-box copy, a single-instance loss (termination, disk
  failure) is a total loss.

  **Make the backup off-box from day one** — on-box `/var/backups` dies
  with the instance, and the DB is the only copy of the family's
  finances. Add a second step to the timer that pushes the newest
  snapshot somewhere else: a **private, authenticated** OCI Object
  Storage bucket via `rclone` with access keys (never a
  pre-authenticated request URL — anyone holding the URL can read it),
  a second VM, or the LAN machine
  (`rsync -a /var/backups/*.age user@lan-host:/backups/houses/` — only
  the age-encrypted ciphertexts ever leave the box, never the plaintext
  snapshots).
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
- **Proxied record with origin TLS**: run Caddy (or any ACME-capable
  proxy) on the origin to terminate TLS with a Let's Encrypt cert, move
  the app to a supported HTTPS origin port (e.g. 8443), and use
  **Full (strict)** — Cloudflare validates the origin cert, so the
  Cloudflare→origin leg is encrypted too.
- **Flexible TLS alone is NOT enough**: it encrypts only browser↔
  Cloudflare; the leg from Cloudflare to the plain-HTTP origin crosses
  the public internet unencrypted. Prefer the tunnel or origin TLS.

Whichever path: **restrict the OCI security list for the app port to
Cloudflare's published IP ranges** (`https://www.cloudflare.com/ips/`)
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
