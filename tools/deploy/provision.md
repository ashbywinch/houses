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

## 1. Oracle Cloud Free Tier box (~30–60 min, may span days for A1)

1. Create the tenancy at cloud.oracle.com (pay-as-you-go with free-tier
   resources, or the always-free tenancy).
2. **Compute → Instances → Create instance**:
   - Image: **Ubuntu 24.04 (arm64)**, Shape: **VM.Standard.A1.Flex**,
     4 OCPU / 24 GB RAM (the free ARM shape — the only free shape that runs
     Chrome comfortably). Boot volume 150–200 GB.
   - If "out of capacity": retry over the day, or try a different region.
     A1 is frequently sold out; do not fight it — the fallback is a $4–6 VPS
     with ≥4 GB RAM (the plan's Phase 0 note).
   - **Reserved public IP** (allocate at creation so it survives reboots —
     the tunnel hostnames and Google OAuth URIs reference it indirectly, but
     the IP must be stable for SSH).
   - SSH key pair: download the `.pem`, `chmod 600`, keep it safe.
3. **VCN security list**: one ingress rule — **22/tcp from 0.0.0.0/0** is
   acceptable ONLY because SSH is key-only (see step 3 of §2; no password
   auth). If you'd rather restrict, GitHub publishes its runner IP ranges
   (api.github.com/meta) — but they rotate; key-only SSH is the simpler
   posture. No other ingress. The tunnel makes outbound-only the norm.
4. SSH in: `ssh -i ~/.ssh/oracle.pem ubuntu@<public-ip>`.

## 2. Box setup (one-time, ~30 min)

Run these on the box as `ubuntu`:

```bash
# deps (same list as the plan doc Phase 2)
sudo apt update && sudo apt install -y python3-venv unzip curl ca-certificates sqlite3 rsync age rclone file nodejs npm git make \
  fonts-liberation libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libxkbcommon0 \
  libxcomposite1 libxdamage1 libgbm1 libasound2
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Chrome for the scraper (arm64 .deb, sanity-checked like the plan doc):
```bash
curl -fL --retry 3 -Lo /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_arm64.deb
file /tmp/chrome.deb | grep -q "Debian binary package" || { echo "bad chrome download"; exit 1; }
sudo dpkg -i /tmp/chrome.deb || sudo apt -f install -y
/usr/bin/google-chrome --headless=new --version || { echo "chrome does not launch headless"; exit 1; }
sudo mkdir -p /var/lib/houses-chrome && sudo chown ubuntu:ubuntu /var/lib/houses-chrome
sudo cp /opt/houses/source/tools/deploy/units/houses-chrome.service /etc/systemd/system/ 2>/dev/null || true
sudo systemctl daemon-reload && sudo systemctl enable --now houses-chrome.service
```

Layout + both checkouts + the deploy tooling:
```bash
sudo mkdir -p /opt/houses/data
sudo chown -R ubuntu:ubuntu /opt/houses
cd /opt/houses
git clone https://github.com/ashbywinch/houses.git blue
git clone https://github.com/ashbywinch/houses.git green
cp blue/tools/deploy/run-instance.sh blue/tools/deploy/release.sh blue/tools/deploy/switch.sh .
chmod +x run-instance.sh release.sh switch.sh
echo blue > ACTIVE          # blue is live from day one
sudo cp blue/tools/deploy/units/houses-blue.service /etc/systemd/system/
sudo cp blue/tools/deploy/units/houses-green.service /etc/systemd/system/
```

Secrets — install the LAN `.env` as root-only `/etc/houses.env`. Use the
cutover pipeline from `docs/deployment-oracle-free-tier.md` Phase 3 (grep out
the sheet-era keys — they crash pydantic at boot). At minimum the critical
keys must be present and non-empty:

```bash
sudo install -o root -g root -m 600 /dev/stdin /etc/houses.env   # paste STRICT KEY=VALUE
# required keys: HOUSES_SESSION_SECRET, HOUSES_GOOGLE_WEB_CLIENT_ID/SECRET,
#                HOUSES_GOOGLE_DEVICE_CLIENT_ID/SECRET, TFL_API_KEY,
#                HEIGIT_API_KEY, PLACES_API_KEY, EPC_BEARER_TOKEN
# plus: HOUSES_HOST=0.0.0.0  HOUSES_PORT=<8765 or 8766 per side — see below>
```

Wait — ports: the run-instance.sh script sets HOUSES_PORT itself (8765/8766
per side), so **do not put HOUSES_PORT in /etc/houses.env** (a value there
would be overridden anyway — process env wins — but keep the file clean).

Copy the live data + DB (from your LAN machine, `make stop` the LAN app
first so the snapshot is the last word — same machinery as the plan doc
Phase 3; the DB will be ~520 MB now that it is compressed):

```bash
sqlite3 data/houses.db ".backup '/tmp/houses-backup.db'"
cat /tmp/houses-backup.db | ssh ubuntu@<ip> "umask 077; cat > /opt/houses/data/houses.db && chmod 600 /opt/houses/data/houses.db && sqlite3 /opt/houses/data/houses.db 'PRAGMA integrity_check;' | grep -q '^ok$'"
rsync -a --exclude 'houses.db*' data/ ubuntu@<ip>:/opt/houses/data/
rm -f /tmp/houses-backup.db
```

Start the live side:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now houses-blue
curl -s --max-time 10 -o /dev/null -w 'blue: %{http_code}\n' http://localhost:8765/health
```

## 3. Cloudflare tunnel + DNS (where blueumbrella.net lives — find out)

The domain is blueumbrella.net and you don't yet know where it is configured.
Find out: `whois blueumbrella.net` shows the registrar; log into that
registrar's DNS console (GoDaddy/Namecheap/Cloudflare/123-reg/…). You do NOT
need to move DNS — a cloudflared tunnel works behind any DNS host via CNAME.

1. On the box, install cloudflared and log in (this opens a browser to
   authorize the account — do it from your laptop if the box is headless:
   `cloudflared tunnel login` needs a browser; the token it writes is
   `~/.cloudflared/cert.pem` — copy it to the box):
   ```bash
   curl -fL --retry 3 -o /tmp/cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
   file /tmp/cloudflared.deb | grep -q "Debian binary package" || { echo "bad download"; exit 1; }
   sudo dpkg -i /tmp/cloudflared.deb
   cloudflared tunnel login
   cloudflared tunnel create houses        # prints a UUID — that is HOUSES_TUNNEL_ID
   sudo mkdir -p /root/.cloudflared
   sudo cp ~/.cloudflared/<UUID>.json /root/.cloudflared/
   ```
2. In the registrar's DNS console add two CNAME records (both point at the
   SAME tunnel hostname — cloudflared's ingress rules distinguish by
   hostname):
   - `houses.blueumbrella.net` → `<UUID>.cfargotunnel.com`
   - `houses-smoke.blueumbrella.net` → `<UUID>.cfargotunnel.com`
3. Configure the tunnel ingress. The switch.sh script renders
   `/etc/cloudflared/config.yml` on every flip — it needs three env vars.
   Persist them for switch.sh (and release.sh's smoke URL):
   ```bash
   sudo sh -c 'echo "HOUSES_MAIN_HOST=houses.blueumbrella.net" >> /etc/houses.env'
   sudo sh -c 'echo "HOUSES_SMOKE_HOST=houses-smoke.blueumbrella.net" >> /etc/houses.env'
   sudo sh -c 'echo "HOUSES_TUNNEL_ID=<UUID>" >> /etc/houses.env'
   ```
   Then install the tunnel as a service:
   ```bash
   sudo cloudflared --config /etc/cloudflared/config.yml service install
   # first generate the config so the service has something to run:
   sudo HOUSES_MAIN_HOST=houses.blueumbrella.net HOUSES_SMOKE_HOST=houses-smoke.blueumbrella.net \
     HOUSES_TUNNEL_ID=<UUID> /opt/houses/switch.sh --noop 2>/dev/null || true   # renders config — see note
   sudo systemctl restart cloudflared
   ```
   (If `switch.sh --noop` is not yet in your copy, render the config by hand
   once — the file is the 4-line template in switch.sh — or just run a real
   flip after the first release; cloudflared refuses to start only if the
   config file is missing, so create it manually once.)
4. Verify from outside your network (phone on cellular):
   `https://houses.blueumbrella.net/health` → `{"status":"ok"}`.

## 4. Google OAuth — allow the prod hostnames

In the Google Cloud console, open the OAuth consent screen → **Authorized
redirect URIs** for the web client you already use (the LAN `.env`'s
HOUSES_GOOGLE_WEB_CLIENT_ID/SECRET — same project, add URIs; no new creds):
- `https://houses.blueumbrella.net/api/auth/callback`
- `https://houses-smoke.blueumbrella.net/api/auth/callback`

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
   action `switch`. Traffic moves to green; the tunnel hostnames swap; blue
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
  is, only that Ubuntu + systemd + cloudflared exist.
- **cloudflared restart on flip** causes a ~2 s tunnel blip — fine for a
  family app; do flips outside peak use if it matters.
- **Rollback restores the pre-flip DB snapshot unconditionally** — anything
  written between flip and rollback is lost by design (deterministic,
  short window). If you need those writes, don't roll back; fix forward.
