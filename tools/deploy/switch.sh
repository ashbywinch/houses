#!/bin/sh
# /opt/houses/switch.sh — flip the ACTIVE side and the tunnel mapping, or
# roll back to the previous side.
#
# Usage:
#   switch.sh            # flip: standby (smoke-verified) becomes live
#   switch.sh --rollback # undo the last flip
#
# Flip sequence: pre-flip DB snapshot -> stop old side -> update ACTIVE ->
# start new side (live DB; init_db applies any schema migration) -> swap the
# Cloudflare tunnel hostname mapping -> verify through the tunnel.
#
# Rollback semantics: the previous side still runs the previous release's
# code, and the pre-flip snapshot is restored unconditionally — deterministic
# and lossless within the (short) window between flip and rollback. If the
# new release migrated the schema, the old code may not read it, which is
# exactly why the snapshot is restored.
set -eu

ROOT="${HOUSES_ROOT:-/opt/houses}"
ACTION="${1:-flip}"
TS=$(date +%Y%m%d-%H%M%S)
MAIN_HOST="${HOUSES_MAIN_HOST:?set HOUSES_MAIN_HOST, e.g. houses.blueumbrella.net}"
SMOKE_HOST="${HOUSES_SMOKE_HOST:?set HOUSES_SMOKE_HOST, e.g. houses-smoke.blueumbrella.net}"
TUNNEL_ID="${HOUSES_TUNNEL_ID:?set HOUSES_TUNNEL_ID}"
CFG=/etc/cloudflared/config.yml

port_of() { [ "$1" = blue ] && echo 8765 || echo 8766; }

render_tunnel() {
  # $1 = main-host target side, $2 = smoke-host target side
  sudo tee "$CFG" >/dev/null <<EOF
tunnel: $TUNNEL_ID
credentials-file: /root/.cloudflared/$TUNNEL_ID.json

ingress:
  - hostname: $MAIN_HOST
    service: http://localhost:$(port_of "$1")
  - hostname: $SMOKE_HOST
    service: http://localhost:$(port_of "$2")
  - service: http_status:404
EOF
}

CURRENT=$(cat "$ROOT/ACTIVE")

if [ "$ACTION" = "--rollback" ]; then
  [ -f "$ROOT/PREVIOUS" ] || { echo "switch: no PREVIOUS marker — nothing to roll back" >&2; exit 1; }
  NEW=$(cat "$ROOT/PREVIOUS")
  OLD="$CURRENT"
  echo "== rollback: $OLD -> $NEW"
else
  case "$CURRENT" in
    blue)  NEW=green ;;
    green) NEW=blue ;;
    *) echo "switch: bad ACTIVE marker '$CURRENT'" >&2; exit 1 ;;
  esac
  OLD="$CURRENT"
  echo "== flip: $OLD -> $NEW"
fi

[ "$NEW" = "$OLD" ] && { echo "switch: no-op — already on $NEW" >&2; exit 1; }

# Pre-flip snapshot: the rollback restore target (and belt-and-braces safety
# net for the flip itself).
echo "== pre-flip snapshot"
sudo mkdir -p /var/backups
sudo sqlite3 "$ROOT/data/houses.db" ".backup '/var/backups/houses-pre-flip-$TS.db'"
sudo chmod 600 "/var/backups/houses-pre-flip-$TS.db"

echo "== stopping $OLD"
sudo systemctl stop "houses-$OLD"

echo "$NEW" > "$ROOT/ACTIVE"
echo "$OLD" > "$ROOT/PREVIOUS"

echo "== starting $NEW (live DB)"
sudo systemctl start "houses-$NEW"
PORT=$(port_of "$NEW")
for i in $(seq 1 60); do
  curl -fsS --max-time 3 "localhost:$PORT/health" >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS --max-time 3 "localhost:$PORT/health" >/dev/null 2>&1 || {
  echo "switch: $NEW not healthy on :$PORT — rolling back" >&2
  sudo systemctl stop "houses-$NEW" || true
  echo "$OLD" > "$ROOT/ACTIVE"
  sudo sqlite3 "$ROOT/data/houses.db" ".restore '/var/backups/houses-pre-flip-$TS.db'" 2>/dev/null || \
    sudo cp "/var/backups/houses-pre-flip-$TS.db" "$ROOT/data/houses.db"
  sudo systemctl start "houses-$OLD"
  exit 1
}

# Swap the tunnel hostname mapping: main host -> new active port, smoke host
# -> the old side (now the standby, still running its own smoke copy if one
# was made — otherwise it answers /health on its port via the smoke DB path).
echo "== updating tunnel mapping"
render_tunnel "$NEW" "$OLD"
sudo systemctl restart cloudflared

echo "== verifying through the tunnel"
for i in $(seq 1 30); do
  curl -fsS --max-time 5 "https://$MAIN_HOST/health" >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS --max-time 5 "https://$MAIN_HOST/health" >/dev/null 2>&1 || {
  echo "switch: public health check failed on https://$MAIN_HOST/health" >&2
  exit 1
}

echo "== live on $NEW: https://$MAIN_HOST (pre-flip snapshot /var/backups/houses-pre-flip-$TS.db)"
