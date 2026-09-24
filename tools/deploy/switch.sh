#!/bin/bash
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
LOG_DIR="${HOUSES_LOG_DIR:-$ROOT/logs/releases}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/switch-$(date +%Y%m%d-%H%M%S)-${ACTION##--}.log"
exec > >(tee -a "$LOG") 2>&1
mark() { echo "== $(date +%H:%M:%S) $*"; logger -t houses-release "switch $*"; }

prune_logs() {
  local keep="${1:-32}"
  find "$LOG_DIR" -maxdepth 1 -name '*.log' -type f \
    | sort -r \
    | tail -n +"$((keep + 1))" \
    | xargs -r rm -f
}

if [ "$ACTION" = "--diagnose" ]; then
  # READ-ONLY box state dump (incident diagnostics). Prints to stdout —
  # the deploy key's forced command relays release.sh/switch.sh stdout, so
  # the workflow log receives this. Never mutates anything; exits before
  # ACTIVE/snapshot logic. Sanctioned command shape: the authorized_keys
  # allowlist is release.sh/switch.sh only.
  echo "===== tooling (sha256) ====="
  sha256sum "$ROOT/release.sh" "$ROOT/switch.sh" "$ROOT/run-migration.sh" 2>&1
  echo "===== /opt/houses/migrations.list ====="
  if [ -f "$ROOT/migrations.list" ]; then cat "$ROOT/migrations.list"; else echo "MISSING (fail-fast guard will refuse flips)"; fi
  echo "===== ACTIVE/PREVIOUS markers ====="
  cat "$ROOT/ACTIVE" 2>&1 || true; cat "$ROOT/PREVIOUS" 2>&1 || true   # PREVIOUS absent on a fresh box — set -e must not abort the dump
  echo "===== unit states ====="
  systemctl is-active houses-blue houses-green 2>&1
  echo "===== provisioned marker ====="
  if [ -f "$ROOT/PROVISIONED" ]; then cat "$ROOT/PROVISIONED"; else echo "not-yet (tooling is up but seed/env/caddy may still be running)"; fi
  echo "===== release marks (newest 12) ====="
  ls -lat "$LOG_DIR"/ 2>&1 | head -13
  echo "----- newest switch log -----"
  tail -n 45 "$(ls -t "$LOG_DIR"/switch-*.log 2>/dev/null | head -1)" 2>&1
  echo "----- newest run-migration log -----"
  tail -n 25 "$(ls -t "$LOG_DIR"/run-migration-*.log 2>/dev/null | head -1)" 2>&1
  echo "===== snapshots ====="
  ls -la /var/backups/ 2>&1
  echo "===== live DB + WAL ====="
  wc -c "$ROOT/data/houses.db" "$ROOT/data/houses.db-wal" "$ROOT/data/houses.db-shm" 2>&1
  stat -c '%y %s %n' "$ROOT/data/houses.db" 2>&1
  echo "===== sides ====="
  ls -la "$ROOT"/ 2>&1 | head -20
  echo "===== journal houses-blue (last 40) ====="
  journalctl -u houses-blue -n 40 --no-pager 2>&1
  echo "===== journal houses-green (last 80) ====="
  journalctl -u houses-green -n 80 --no-pager 2>&1
  exit 0
fi

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
mark "pre-flip snapshot"
sudo mkdir -p /var/backups
# Bounded backup API (never the CLI's .backup): the flip must never wait
# unbounded on the live DB for its snapshot.
BACKUP_PY=$(mktemp "$ROOT/.backup-XXXXXX.py")
sudo chown ubuntu:ubuntu "$BACKUP_PY"
sudo sh -c "cat > '$BACKUP_PY'" <<'PY'
import sqlite3, sys, time
live, out = sys.argv[1], sys.argv[2]
src = sqlite3.connect(live, timeout=5)  # write-capable: a PASSIVE checkpoint must apply the WAL for the copy to include it
try:
    src.execute("PRAGMA wal_checkpoint(PASSIVE)")
except sqlite3.OperationalError:
    pass
dst = sqlite3.connect(out)
deadline = time.monotonic() + 300
aborted = [False]
def _progress(*_a, **_k):
    if time.monotonic() > deadline:
        aborted[0] = True
        return 1  # abort
    return 0
try:
    src.backup(dst, pages=1000, progress=_progress)
    if aborted[0]:
        sys.exit("backup exceeded the deadline")
    rows = dst.execute("SELECT count(*) FROM node_results").fetchone()[0]
    if rows == 0:
        sys.exit("snapshot copy has no node_results rows — refusing a stale/empty standby")
    print(f"snapshot ok: {rows} rows in node_results")
except sqlite3.OperationalError as e:
    sys.exit(f"backup failed within the deadline: {e}")
finally:
    dst.close()
    src.close()


PY
if ! sudo "$ROOT/$CURRENT/.venv/bin/python" "$BACKUP_PY" "$ROOT/data/houses.db" "$SNAPSHOT"; then
  rm -f "$BACKUP_PY"
  mark "pre-flip snapshot failed within the deadline — aborting flip (live DB untouched)"
  exit 1
fi
rm -f "$BACKUP_PY"
mark "stopping $OLD"
sudo systemctl stop "houses-$OLD"

# Data migrations on the LIVE DB — with prod STOPPED (no readers or
# writers to tear; a write during the batched re-key would re-create
# pre-migration rows and fail the verify). The SAME ordered list as
# release.sh; the box's migrations.list is shippped by the release (this
# ref's copy is the authority the flip uses). On failure the pre-flip
# snapshot is restored UNCONDITIONALLY and the old side comes back —
# never leave a half-migrated DB in front of either code.
if [ "$ACTION" != "--rollback" ]; then
  # FAIL-FAST: the release SHIPS /opt/houses/migrations.list; its absence
  # means tooling drift — never flip with a silently-skipped migration
  # (the v1.5.x incident).
  if [ ! -f "$ROOT/migrations.list" ]; then
    mark "migrations.list missing on the box — the release did not ship it; refusing to flip"
    exit 1
  fi
  while IFS= read -r MIG; do
    [ -z "$MIG" ] && continue
    [[ "$MIG" == \#* ]] && continue  # migrations.list carries a # header — never a migration path
    mark "run data migration on the LIVE DB: $MIG"
    if ! sudo /opt/houses/run-migration.sh "$ROOT/$NEW/$MIG" "$ROOT/data/houses.db" "$ROOT/$NEW/.venv/bin/python"; then
      mark "migration FAILED on the live DB — restoring the pre-flip snapshot and the old side"
      rm -f "$ROOT/data/houses.db-wal" "$ROOT/data/houses.db-shm"
      sudo cp "$SNAPSHOT" "$ROOT/data/houses.db"
      sudo chmod 600 "$ROOT/data/houses.db"
      sudo chown ubuntu:ubuntu "$ROOT/data/houses.db"
      sudo systemctl restart "houses-$OLD"
      exit 1
    fi
  done < "$ROOT/migrations.list"
fi

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

mark "restarting $NEW on the live DB"
# restart, not start: the standby may be running (pre-2026-09-12 releases
# left it warm) or stopped (R2: ephemeral) — `start` would no-op on a
# running unit and the unit would keep its stale environment. A restart
# makes run-instance.sh re-read ACTIVE (live DB + :8765) either way.
sudo systemctl restart "houses-$NEW"
PORT=8765  # the new ACTIVE side binds 8765 (role-based ports)
for i in $(seq 1 60); do
  curl -fsS --max-time 3 "localhost:$PORT/health" >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS --max-time 3 "localhost:$PORT/health" >/dev/null 2>&1 || {
  mark "switch: $NEW not healthy on :$PORT — rolling back"
    journalctl -u "houses-$NEW" --since="5 minutes ago" --no-pager 2>/dev/null | tail -30 || true
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
  prune_logs
mark "live on $NEW: https://$MAIN_HOST (pre-flip snapshot $SNAPSHOT)"
else
  echo "WARNING: https check failed — the app is up locally; check the DNS A record and Caddy's cert state (journalctl -u caddy)."
fi
