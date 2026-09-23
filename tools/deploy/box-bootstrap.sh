#!/bin/bash
# /opt/houses bootstrap — rendered as cloud-init userdata by the
# provision workflow (tools/deploy/provision-box.sh) and executed ONCE on
# first boot of a fresh instance. This is the ENTIRE box build: packages,
# checkouts, layout/units, the deploy-key allowlist, the data+env restore
# from the off-box backup, the Cloudflare tunnel, and the backup units.
# Idempotent-safe (re-run converges), designed for first boot.
#
# Inputs arrive as environment variables baked into the userdata by the
# provision workflow. They live in Oracle instance metadata, which is only
# readable from inside the instance — the standard cloud-init secret
# pattern. The box itself never stores them beyond this boot.
set -euo pipefail

log "0. inputs"
: "${PROVISION_REF:?} ${DEPLOY_PUBKEY:?} ${AGE_KEY:?} ${AGE_RECIPIENT:?} ${CF_TUNNEL_TOKEN:?} ${RCLONE_CONFIG:?}"
export HOUSES_ROOT=/opt/houses

log "1. packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3-venv python3-pip sqlite3 age rclone unzip curl ca-certificates openssh-client >/dev/null

log "2. uv (absolute path /home/ubuntu/.local/bin/uv — PATH-independent)"
install -d -o ubuntu -g ubuntu /home/ubuntu/.local/bin
sudo -u ubuntu -H curl -LsSf https://astral.sh/uv/install.sh | sudo -u ubuntu -H env HOME=/home/ubuntu sh -s -- --no-modify-path >/dev/null
[ -x /home/ubuntu/.local/bin/uv ] || { echo "uv install failed" >&2; exit 1; }

log "3. cloudflared (outbound-only tunnel; no inbound ports beyond 22)"
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" > /etc/apt/sources.list.d/cloudflared.list
apt-get update -qq && apt-get install -y -qq cloudflared >/dev/null

log "4. repo checkouts at $PROVISION_REF"
mkdir -p /opt/houses
for side in blue green; do
  if [ ! -d "/opt/houses/$side/.git" ]; then
    git clone -q https://github.com/ashbywinch/houses "/opt/houses/$side"
  fi
  git -C "/opt/houses/$side" fetch -q origin
  git -C "/opt/houses/$side" checkout -q "$PROVISION_REF" || git -C "/opt/houses/$side" checkout -q -b provision "$PROVISION_REF"
  git -C "/opt/houses/$side" clean -qfd
done
chown -R ubuntu:ubuntu /opt/houses

log "5. layout + units + sudoers (idempotent)"
bash /opt/houses/blue/tools/deploy/box-setup.sh
log "6. deploy-key allowlist ($DEPLOY_PUBKEY)"
/opt/houses/install-deploy-allowlist.sh "$DEPLOY_PUBKEY"
# the operator's interactive admin key (plain append — break-glass via
# SSH; the OCI serial console is the last resort)
OPERATOR_HOME=$(getent passwd ubuntu | cut -d: -f6)
mkdir -p "$OPERATOR_HOME/.ssh"
grep -qF "$OPERATOR_PUBKEY" "$OPERATOR_HOME/.ssh/authorized_keys" 2>/dev/null || \
  printf '%s\n' "$OPERATOR_PUBKEY" >> "$OPERATOR_HOME/.ssh/authorized_keys"
chown -R ubuntu:ubuntu "$OPERATOR_HOME/.ssh"
chmod 700 "$OPERATOR_HOME/.ssh" && chmod 600 "$OPERATOR_HOME/.ssh/authorized_keys"

log "7. data + env restore from houses:backups (age-decrypted)"
install -d -o ubuntu -g ubuntu /opt/houses/data
# rclone remote "houses" — config from the secret (mirrors the Phase 6
# backup-push unit, which uses "houses:backups/" verbatim). Written to
# BOTH ubuntu and root config (the app reads as ubuntu; the backup units
# run as root).
mkdir -p /home/ubuntu/.config/rclone /root/.config/rclone
{
  echo "[houses]"
  echo "$RCLONE_CONFIG"
} | tee /home/ubuntu/.config/rclone/rclone.conf >/root/.config/rclone/rclone.conf
chown -R ubuntu:ubuntu /home/ubuntu/.config
# backup units need the age RECIPIENT (public half) — /etc/houses-backup.env
cat > /etc/houses-backup.env <<ENV
RECIPIENT=$AGE_RECIPIENT
ENV
chmod 600 /etc/houses-backup.env
NEWEST_DB=$(rclone lsl houses:backups/ 2>/dev/null | grep 'houses-.*\.db\.age$' | sort -k2 | tail -1 | awk '{print $NF}')
[ -n "$NEWEST_DB" ] || { echo "no .db.age in houses:backups/ — refusing to build a box without data" >&2; exit 1; }
log "restoring $NEWEST_DB"
age -d -i <(printf '%s\n' "$AGE_KEY") -o /opt/houses/data/houses.db < <(rclone cat "houses:backups/$NEWEST_DB")
sqlite3 /opt/houses/data/houses.db "PRAGMA integrity_check;" | grep -q '^ok$' || { echo "restored DB failed integrity check" >&2; exit 1; }
NEWEST_ENV=$(rclone lsl houses:backups/ 2>/dev/null | grep 'houses-.*\.env\.age$' | sort -k2 | tail -1 | awk '{print $NF}')
[ -n "$NEWEST_ENV" ] || { echo "no .env.age in houses:backups/ — cannot restore secrets" >&2; exit 1; }
log "restoring $NEWEST_ENV"
age -d -i <(printf '%s\n' "$AGE_KEY") -o /etc/houses.env < <(rclone cat "houses:backups/$NEWEST_ENV")
chmod 600 /etc/houses.env
chown root:root /etc/houses.env
install -d -o ubuntu -g ubuntu /var/backups

log "8. Cloudflare tunnel (remotely-managed: token only, no config files)"
install -d -o root -g root /etc/cloudflared
printf '%s\n' "$CF_TUNNEL_TOKEN" > /etc/cloudflared/token
chmod 600 /etc/cloudflared/token
cat > /etc/systemd/system/cloudflared.service <<'UNIT'
[Unit]
Description=cloudflared tunnel
After=network-online.target
Wants=network-online.target
[Service]
ExecStart=/usr/bin/cloudflared tunnel --no-autoupdate run --token /etc/cloudflared/token
Restart=on-failure
RestartSec=5
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now cloudflared.service

log "9. backup units + timer (nightly on-box snapshot + off-box push)"
cp /opt/houses/blue/tools/deploy/units/houses-backup.service /etc/systemd/system/
cp /opt/houses/blue/tools/deploy/units/houses-backup-push.service /etc/systemd/system/
cp /opt/houses/blue/tools/deploy/units/houses-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now houses-backup.timer

log "10. provision marker"
echo "provisioned $(date -u +%FT%TZ) ref=$PROVISION_REF" > /opt/houses/PROVISIONED
chmod 644 /opt/houses/PROVISIONED

log "PROVISION COMPLETE — tunnel, restore, and allowlist are live"