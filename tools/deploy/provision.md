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

## 1. Oracle Cloud Free Tier box — Terraform (account + API key are the only manual bits)

The whole Oracle side (VCN, security list, instance, reserved IP, cloud-init
box setup) is `terraform/` in the repo. Your only manual steps:

1. **Create the account** at cloud.oracle.com (**Start for free**; credit
   card for identity only — Free Tier doesn't charge it).
2. **The API signing key** (one console touch, ~5 min):
   ```bash
   mkdir -p ~/.oci && cd ~/.oci
   openssl genrsa -out oci_api_key.pem 2048 && chmod 600 oci_api_key.pem
   openssl rsa -pubout -in oci_api_key.pem -out oci_api_key_public.pem
   ```
   Console → your profile → **API keys → Add API key** → upload
   `oci_api_key_public.pem`. It shows a **fingerprint** (copy it). Also copy
   your **Tenancy OCID** (profile → Tenancy) and **User OCID** (profile →
   User settings). Region: pick one with A1 capacity (US regions usually;
   eu-frankfurt-1 often works) — retry over a day if `apply` says
   out-of-capacity; the fallback is a $4–6 VPS with ≥4 GB RAM.
3. **The SSH key** for the box (this machine):
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/oracle -N "" -C "oracle-houses"
   ```
4. **Fill the variables** and apply (terraform on this machine — install
   once: `brew install terraform` or the HashiCorp binary):
   ```bash
   cd terraform
   cp terraform.tfvars.example terraform.tfvars   # fill the OCIDs/fingerprint/paths
   terraform init
   terraform plan     # read it — security list is SSH-only, shape is A1.Flex
   terraform apply
   terraform output ssh_command   # -> ssh -i ~/.ssh/oracle ubuntu@<ip>
   ```
   `apply` runs cloud-init: apt deps, Chrome, cloudflared, uv, the two
   checkouts (/opt/houses/blue + green), units, ACTIVE=blue. It can take
   ~5–10 min after the instance boots (watch with
   `ssh ubuntu@<ip> "sudo tail -f /var/log/cloud-init-output.log"`).

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
   ```
   **Do not put HOUSES_PORT in /etc/houses.env** — run-instance.sh sets it
   per side (8765/8766). Add the tunnel vars too (Step 3's env block).
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
