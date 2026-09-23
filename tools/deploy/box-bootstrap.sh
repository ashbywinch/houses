#!/bin/bash
# /opt/houses bootstrap — rendered as the GCP startup-script by
# tools/deploy/provision-box.sh and executed ONCE at first boot of a fresh
# instance. The ENTIRE box build: packages, checkouts, layout/units, the
# deploy-key allowlist + operator admin key, Caddy HTTPS (:443 — the box's
# own proven path; there is no tunnel), and a data seed restore from GCS
# (the migrated DB). Idempotent-safe.
#
# ORDER MATTERS (2026-09-23 postmortem): the two SSH paths — the operator
# admin key and the workflow's allowlisted deploy key — are installed
# BEFORE the heavy steps, so even a half-provisioned box is reachable and
# troubleshootable. The old box died unreadable: its allowlist swallowed
# arg-bearing commands, its bootstrap died before the checkouts.
#
# Inputs arrive as environment variables baked into the startup-script by
# the provision workflow (GCP instance metadata — readable only inside
# the project; the seed SA key is the one secret it carries).
set -euo pipefail

log() { echo "== $*"; }

log "0. inputs"
: "${PROVISION_REF:?} ${OPERATOR_PUBKEY:?}"
DEPLOY_PUBKEY="${DEPLOY_PUBKEY:-}"
export HOUSES_ROOT=/opt/houses

log "1. packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3-venv python3-pip sqlite3 curl ca-certificates gpg openssh-client >/dev/null

log "2. repo checkouts at $PROVISION_REF"
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

log "3. layout + units + sudoers (idempotent)"
bash /opt/houses/blue/tools/deploy/box-setup.sh

log "4. SSH paths FIRST — the box is ssh-able even if the rest fails"
# 4a. operator admin key (interactive break-glass)
OPERATOR_HOME=$(getent passwd ubuntu | cut -d: -f6)
mkdir -p "$OPERATOR_HOME/.ssh"
grep -qF "$OPERATOR_PUBKEY" "$OPERATOR_HOME/.ssh/authorized_keys" 2>/dev/null || \
  printf '%s\n' "$OPERATOR_PUBKEY" >> "$OPERATOR_HOME/.ssh/authorized_keys"
chown -R ubuntu:ubuntu "$OPERATOR_HOME/.ssh"
chmod 700 "$OPERATOR_HOME/.ssh" && chmod 600 "$OPERATOR_HOME/.ssh/authorized_keys"
# 4b. the workflow's restricted deploy key (allowlist: release.sh/switch.sh ONLY)
if [ -n "$DEPLOY_PUBKEY" ]; then
  /opt/houses/install-deploy-allowlist.sh "$DEPLOY_PUBKEY"
fi

log "5. uv (absolute path /home/ubuntu/.local/bin/uv — PATH-independent)"
install -d -o ubuntu -g ubuntu /home/ubuntu/.local/bin
sudo -u ubuntu -H curl -LsSf https://astral.sh/uv/install.sh | sudo -u ubuntu -H env HOME=/home/ubuntu sh -s -- --no-modify-path >/dev/null
[ -x /home/ubuntu/.local/bin/uv ] || { echo "uv install failed" >&2; exit 1; }

log "6. data seed from GCS (the migrated DB produced by the seed action)"
install -d -o ubuntu -g ubuntu /opt/houses/data
if [ -n "${SEED_SA_KEY:-}" ]; then
  printf '%s' "$SEED_SA_KEY" | base64 -d > /tmp/seed-sa.json 2>/dev/null
  gcloud auth activate-service-account --key-file=/tmp/seed-sa.json --project="${SEED_PROJECT:-houses-498215}" >/dev/null 2>&1 || true
  rm -f /tmp/seed-sa.json
  for attempt in 1 2 3; do
    if gsutil -q cp "${GCS_SEED:-gs://houses-seed/latest.db}" /opt/houses/data/houses.db 2>/dev/null; then
      break
    fi
    sleep 5
  done
  if [ -s /opt/houses/data/houses.db ]; then
    chown ubuntu:ubuntu /opt/houses/data/houses.db && chmod 600 /opt/houses/data/houses.db
    sqlite3 /opt/houses/data/houses.db "PRAGMA integrity_check;" | grep -q '^ok$' || { echo "seed failed integrity check" >&2; exit 1; }
    log "seeded from $GCS_SEED"
  else
    log "no seed at $GCS_SEED — box starts empty (deploy still runs; seed action refreshes it)"
  fi
else
  log "no seed SA — box starts empty"
fi

log "7. HTTPS — Caddy on :443 (the proven path: the box's own terraform
startup calls install-caddy.sh; the Cloudflare A records proxy to origin
443; NO tunnel exists or is needed)."
if [ -f /etc/houses.env ]; then
  bash /opt/houses/blue/tools/deploy/install-caddy.sh
else
  log "no /etc/houses.env yet — caddy install deferred to the env/deploy step"
fi

log "8. provision marker"
echo "provisioned $(date -u +%FT%TZ) ref=$PROVISION_REF" > /opt/houses/PROVISIONED
chmod 644 /opt/houses/PROVISIONED

log "PROVISION COMPLETE — ssh paths live, seed restored, box ready for deploy"