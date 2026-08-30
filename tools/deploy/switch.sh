#!/bin/sh
# /opt/houses/switch.sh — flip the ACTIVE side, or roll back to the previous side.
#
# Usage:
#   switch.sh            # flip: standby (smoke-verified) becomes live
#   switch.sh --rollback # undo the last flip
#
# Flip sequence: pre-flip DB snapshot -> stop old side -> update ACTIVE ->
# start new side (live DB; init_db applies any schema migration) -> health
# check. Caddy's config is static (hostnames -> role-based ports), so a flip
# never touches TLS or DNS.
#
# Rollback semantics: the previous side still runs the previous release's
# code, and the newest pre-flip snapshot is restored unconditionally —
# deterministic and lossless within the (short) window between flip and
# rollback. If the new release migrated the schema, the old code may not
# read it, which is exactly why the snapshot is restored.
set -eu

ROOT="${HOUSES_ROOT:-/opt/houses}"
ACTION="${1:-flip}"
TS=$(date +%Y%m%d-%H%M%S)

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
SNAPSHOT="/var/backups/houses-pre-flip-$TS.db"
echo "== pre-flip snapshot"
sudo mkdir -p /var/backups
sudo sqlite3 "$ROOT/data/houses.db" ".backup '$SNAPSHOT'"
sudo chmod 600 "$SNAPSHOT"

echo "== stopping $OLD"
sudo systemctl stop "houses-$OLD"

# Rollback also restores the newest pre-flip snapshot BEFORE the old side
# starts: the released side may have migrated the schema, and the contract
# is "restored unconditionally". The snapshot taken above is the current
# side's own pre-flip backup — for a rollback the NEWEST older snapshot is
# the one that predates the flip being undone.
if [ "$ACTION" = "--rollback" ]; then
  OLDEST=$(ls -1t /var/backups/houses-pre-flip-*.db 2>/dev/null | tail -n +2 | head -1 || true)
  RESTORE=${OLDEST:-}
  if [ -n "$RESTORE" ] && [ "$RESTORE" != "$SNAPSHOT" ]; then
    echo "== restoring pre-flip snapshot $RESTORE"
    rm -f "$ROOT/data/houses.db-wal" "$ROOT/data/houses.db-shm"
    sudo cp "$RESTORE" "$ROOT/data/houses.db"
    sudo chmod 600 "$ROOT/data/houses.db"
    sudo chown ubuntu:ubuntu "$ROOT/data/houses.db"
  else
    echo "WARNING: no pre-flip snapshot from before the flip found — rolling back without a DB restore" >&2
  fi
fi

echo "$NEW" > "$ROOT/ACTIVE"
echo "$OLD" > "$ROOT/PREVIOUS"

echo "== restarting $NEW on the live DB"
# restart, not start: the standby has been RUNNING (as the smoke target
# on the smoke DB + :8766) — `start` would no-op and the unit would keep
# its stale environment.  A restart makes run-instance.sh re-read ACTIVE
# (live DB + :8765).
sudo systemctl restart "houses-$NEW"
PORT=8765  # the new ACTIVE side binds 8765 (role-based ports)
for i in $(seq 1 60); do
  curl -fsS --max-time 3 "localhost:$PORT/health" >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS --max-time 3 "localhost:$PORT/health" >/dev/null 2>&1 || {
  echo "switch: $NEW not healthy on :$PORT — rolling back" >&2
  sudo systemctl stop "houses-$NEW" || true
  echo "$OLD" > "$ROOT/ACTIVE"
  sudo cp "$SNAPSHOT" "$ROOT/data/houses.db"
  sudo chmod 600 "$ROOT/data/houses.db"
  # The restored snapshot is root-owned; the app unit runs as ubuntu and
  # cannot open it — a failed flip must not become an outage (PR #68
  # review; matches the --rollback path).
  sudo chown ubuntu:ubuntu "$ROOT/data/houses.db"
  sudo systemctl start "houses-$OLD"
  exit 1
}

# Best-effort public check: the flip is correct once the local health probe
# passed; a failure here is DNS or a not-yet-issued cert, not the flip.
MAIN_HOST=$(grep '^HOUSES_MAIN_HOST=' /etc/houses.env 2>/dev/null | head -1 | cut -d= -f2-)
MAIN_HOST=${MAIN_HOST:-houses.blueumbrella.net}
echo "== verifying https://$MAIN_HOST (best-effort)"
if curl -fsS --max-time 8 "https://$MAIN_HOST/health" >/dev/null 2>&1; then
  echo "== live on $NEW: https://$MAIN_HOST (pre-flip snapshot $SNAPSHOT)"
else
  echo "WARNING: https check failed — the app is up locally; check the DNS A record and Caddy's cert state (journalctl -u caddy)."
fi
