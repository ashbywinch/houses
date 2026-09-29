#!/bin/bash
# /opt/houses/switch.sh — the standby-side control plane of a cutover.
#
#   switch.sh --snapshot   # FREEZE this box (stop the app), then stream its live
#                          # DB to STDOUT; the row count goes to stderr
#   switch.sh --unfreeze   # the abort path: start the app again
#   switch.sh --rebase [<rows>]
#                          # restore the snapshot on STDIN, verify it, migrate +
#                          # check, then start the app (the cutover's steps 2-3)
#   switch.sh --restore <gs://…>  # the EXCEPTION path: same as --rebase, but the
#                          # database comes from a named object instead of a live
#                          # snapshot — for a recovery when the owner's data is
#                          # not trustworthy and must not be carried forward
#   switch.sh --diagnose   # read-only box state dump (artifact, seed, restored)
#
# The FLIP is not here. Traffic moves by pointing the L4 forwarding rules at this
# instance's target (`gcloud … forwarding-rules set-target`, run by CI with its
# own credentials); the box never touches routes and holds no GCP key. The role of
# a box — owner or standby — is exactly that rule's target
# (docs/anti-fragile-rollout-plan.md, Phase 2).
#
# A cutover is: CI freezes the OWNER (--snapshot stops the app, so no write can
# land between the freeze and the copy — zero write loss), pipes that snapshot
# here (--rebase: restore + verify + migrate + check, app stopped), starts the app,
# flips the rules, and watches public health through the settle window. Any
# failure after the freeze leaves the standby's app stopped and CI restarts the
# owner (--unfreeze) — a box can never unfreeze production on its own.
set -euo pipefail

ROOT="${HOUSES_ROOT:-/opt/houses}"
APP="$ROOT/app"
PY="$APP/.venv/bin/python"
DB="$ROOT/data/houses.db"
ACTION="${1:-}"
TS=$(date +%Y%m%d-%H%M%S)
LOG_DIR="${HOUSES_LOG_DIR:-$ROOT/logs/releases}"
mkdir -p "$LOG_DIR"

# ── --diagnose: EVERY probe is best-effort (a fresh box has no ARTIFACT, no
# RESTORED and no logs) — the dump must never abort mid-way.
if [ "$ACTION" = "--diagnose" ]; then
  set +e
  echo "===== identity ====="
  echo "host=$(hostname) time=$(date -u +%FT%TZ)"
  echo "----- /opt/houses/ARTIFACT (which build this box runs) -----"
  if [ -f "$ROOT/ARTIFACT" ]; then cat "$ROOT/ARTIFACT"; else echo "none (no artifact installed yet)"; fi
  echo "----- /opt/houses/SEED (what this box was bootstrapped from) -----"
  if [ -f "$ROOT/SEED" ]; then cat "$ROOT/SEED"; else echo "none (not bootstrapped from a seed)"; fi
  echo "----- /opt/houses/RESTORED (which database this box was last restored to) -----"
  if [ -f "$ROOT/RESTORED" ]; then cat "$ROOT/RESTORED"; else echo "none (bootstrap seed, never restored by a cutover)"; fi
  echo "----- /opt/houses/BASE-IMAGE (the baked OS layer this box was created from) -----"
  if [ -f "$ROOT/BASE-IMAGE" ]; then cat "$ROOT/BASE-IMAGE"; else echo "none (built from the stock image)"; fi
  echo "----- /opt/houses/PROVISIONED -----"
  if [ -f "$ROOT/PROVISIONED" ]; then cat "$ROOT/PROVISIONED"; else echo "not-yet (tooling is up but the bootstrap is still running)"; fi
  echo "===== tooling (sha256) ====="
  sha256sum "$ROOT/install-artifact.sh" "$ROOT/switch.sh" "$ROOT/run_migrations.py" "$ROOT/run-instance.sh" 2>&1
  echo "===== /opt/houses/migrations.list ====="
  if [ -f "$ROOT/migrations.list" ]; then cat "$ROOT/migrations.list"; else echo "MISSING (the runner refuses an empty manifest)"; fi
  echo "===== app unit ====="
  systemctl is-active houses.service 2>&1
  systemctl is-enabled houses.service 2>&1
  echo "===== app health (local) ====="
  curl -fsS --max-time 5 localhost:8765/health 2>&1 | head -c 400; echo
  echo "===== layout ====="
  ls -la "$ROOT"/ 2>&1 | head -20
  echo "===== live DB + WAL ====="
  wc -c "$ROOT/data/houses.db" "$ROOT/data/houses.db-wal" "$ROOT/data/houses.db-shm" 2>&1
  stat -c '%y %s %n' "$ROOT/data/houses.db" 2>&1
  echo "===== disk ====="
  df -h "$ROOT" 2>&1
  echo "===== release marks (newest 12) ====="
  ls -lat "$LOG_DIR"/ 2>&1 | head -13
  echo "----- newest install-artifact log (tail 40) -----"
  tail -n 40 "$(ls -t "$LOG_DIR"/install-artifact-*.log 2>/dev/null | head -1)" 2>&1
  echo "----- newest run-migrations log (tail 40) -----"
  tail -n 40 "$(ls -t "$LOG_DIR"/run-migrations-*.log 2>/dev/null | head -1)" 2>&1
  echo "----- newest switch log (tail 45) -----"
  tail -n 45 "$(ls -t "$LOG_DIR"/switch-*.log 2>/dev/null | head -1)" 2>&1
  echo "===== journal houses.service (last 60) ====="
  journalctl -u houses.service -n 60 --no-pager 2>&1
  exit 0
fi

LOG="$LOG_DIR/switch-${TS}-${ACTION##--}.log"
mark() { echo "== $(date -u +%FT%TZ) $*"; logger -t houses-rollout "switch $*" 2>/dev/null || true; }

[ -x "$PY" ] || { mark "FAILED: no venv at $PY — install the artifact first"; exit 1; }
[ "$(id -u)" = 0 ] || { mark "FAILED: run as root"; exit 1; }
# --snapshot's STDOUT is the .backup bytes and NOTHING else (CI writes them
# straight to a file), so that action logs to stderr only; every other action tees
# both streams into the log.
[ "$ACTION" = "--snapshot" ] || exec > >(tee -a "$LOG") 2>&1

# Bounded copy of the live DB via the stdlib .backup API (never the CLI, whose
# retry loop is unbounded — the 2026-09-07 90-minute hang). WAL-consistent (a
# PASSIVE checkpoint first, which never blocks a writer) with a hard deadline.
# Prints "snapshot ok: N rows in node_results" on stderr: that count is what the
# restore is verified against.
_snapshot() {
  out="$1"
  "$PY" - "$DB" "$out" <<'PY'
import sqlite3, sys, time
live, out = sys.argv[1], sys.argv[2]
src = sqlite3.connect(live, timeout=5)  # write-capable: the checkpoint must apply the WAL for the copy to include it
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
        sys.exit("snapshot copy has no node_results rows — refusing a stale/empty snapshot")
    print(f"snapshot ok: {rows} rows in node_results", file=sys.stderr)
except sqlite3.OperationalError as e:
    sys.exit(f"backup failed within the deadline: {e}")
finally:
    dst.close()
    src.close()
PY
}

# ── the normal cutover: freeze + copy out ──────────────────────────────
if [ "$ACTION" = "--snapshot" ]; then
  mark "snapshot: FREEZING production (stopping houses.service)" >&2
  systemctl stop houses.service || { mark "FAILED: could not stop the app — refusing to snapshot a moving database" >&2; exit 1; }
  SNAP=$(mktemp /var/tmp/houses-snapshot-XXXXXX.db)
  trap 'rm -f "$SNAP"' EXIT
  set +e
  MSG=$(_snapshot "$SNAP" 2>&1 >/dev/null)
  SNAP_RC=$?
  set -e
  printf '%s\n' "$MSG" | tee -a "$LOG" >&2
  if [ "$SNAP_RC" != 0 ]; then
    printf '%s\n' "== snapshot FAILED — production is FROZEN; restart it with: switch.sh --unfreeze" | tee -a "$LOG" >&2
    exit 1
  fi
  # The row count is already on stderr (see the message above); the bytes go out
  # on stdout to CI, which pipes them into --rebase on the standby.
  cat "$SNAP"
  exit 0
fi

# ── the abort path: serve again ────────────────────────────────────────
if [ "$ACTION" = "--unfreeze" ]; then
  mark "unfreeze: starting the app"
  systemctl start houses.service
  for i in $(seq 1 60); do
    curl -fsS --max-time 3 localhost:8765/health >/dev/null 2>&1 && break
    sleep 2
  done
  curl -fsS --max-time 30 localhost:8765/health >/dev/null 2>&1 || {
    mark "FAILED: the app did not come back after --unfreeze — this box is DOWN; check it by hand"
    journalctl -u houses.service --since="5 minutes ago" --no-pager 2>/dev/null | tail -30 || true
    exit 1
  }
  mark "UNFROZEN: app healthy on 127.0.0.1:8765 — production serves from this box again"
  exit 0
fi

# ── the restore body, shared by --rebase (stdin) and --restore (object) ─
# $1 = the staged database, $2 = the expected row count ("" for an object restore,
# where nobody recorded one), $3 = what to record in RESTORED.
_finish_restore() {
  STAGED="$1"
  EXPECTED_ROWS="$2"
  SOURCE="$3"
  [ -s "$STAGED" ] || { mark "FAILED: the staged database is empty"; exit 1; }
  mark "restore: staged $(du -h "$STAGED" | cut -f1)"

  # Verify BEFORE the swap: every later step (migrations, checks, serving) runs
  # against this database, so a bad copy must abort here.
  INTEGRITY=$(sqlite3 "$STAGED" "PRAGMA integrity_check;" | head -1)
  ROWS=$(sqlite3 "$STAGED" "SELECT count(*) FROM node_results;" 2>/dev/null || echo 0)
  mark "restore ok: integrity_check: $INTEGRITY rows: $ROWS"
  if [ "$INTEGRITY" != "ok" ]; then
    mark "FAILED: the restored database fails integrity_check — refusing to install it"
    exit 1
  fi
  if [ "$ROWS" = "0" ]; then
    mark "FAILED: the restored database has no node_results rows — refusing to install it"
    exit 1
  fi
  if [ -n "$EXPECTED_ROWS" ] && [ "$ROWS" != "$EXPECTED_ROWS" ]; then
    mark "FAILED: restored $ROWS rows, the snapshot captured $EXPECTED_ROWS — the transfer lost data"
    exit 1
  fi

  rm -f "$DB-wal" "$DB-shm"
  mv "$STAGED" "$DB"
  chown ubuntu:ubuntu "$DB"
  chmod 600 "$DB"
  printf 'source=%s rows=%s restored_at=%s\n' "$SOURCE" "$ROWS" "$(date -u +%FT%TZ)" > "$ROOT/RESTORED"
  chmod 644 "$ROOT/RESTORED"
  mark "restore: $DB replaced ($(cat "$ROOT/RESTORED"))"

  # Migrations on the quiescent copy — apply AND check every migration. The
  # runner's summary line is the gate: a zero exit alone is not enough.
  mark "restore: migrations on the restored DB (one run_migrations.py call)"
  MIG_RC=0
  MIG_OUT=$("$PY" "$ROOT/run_migrations.py" \
    --manifest "$ROOT/migrations.list" \
    --db "$DB" \
    --scripts-dir "$APP" \
    --python "$PY" \
    --apply 2>&1) || MIG_RC=$?
  printf '%s\n' "$MIG_OUT"
  if [ "$MIG_RC" != 0 ] || ! printf '%s\n' "$MIG_OUT" | grep -Eq '^migrations: [0-9]+ applied\+checked, 0 failed$'; then
    mark "FAILED: the restored DB did not migrate cleanly — the app stays stopped, the owner is untouched"
    exit 1
  fi

  # The cutover's "start the standby app": folded in so the app can only ever come
  # up on a restored, verified, migrated database.
  mark "restore: starting the app on the restored DB"
  systemctl restart houses.service
  for i in $(seq 1 60); do
    curl -fsS --max-time 3 localhost:8765/health >/dev/null 2>&1 && break
    sleep 2
  done
  curl -fsS --max-time 30 localhost:8765/health >/dev/null 2>&1 || {
    mark "FAILED: the app did not become healthy after the restore — leaving it stopped, the owner is untouched"
    journalctl -u houses.service --since="5 minutes ago" --no-pager 2>/dev/null | tail -30 || true
    systemctl stop houses.service || true
    exit 1
  }
  mark "RESTORE READY: rows=$ROWS integrity=$INTEGRITY migrations applied+checked; app healthy on 127.0.0.1:8765"
  exit 0
}

# ── the normal cutover: restore the live snapshot piped in by CI ────────
if [ "$ACTION" = "--rebase" ]; then
  EXPECTED_ROWS="${2:-}"
  [ ! -t 0 ] || { mark "FAILED: --rebase reads the snapshot on stdin (pipe it in)"; exit 1; }
  mark "rebase: stopping the app (nothing writes while the DB is replaced)"
  systemctl stop houses.service 2>/dev/null || true
  STAGED="$ROOT/data/.rebase-$TS.db"
  trap 'rm -f "$STAGED"' EXIT
  mark "rebase: receiving the snapshot on stdin"
  cat > "$STAGED"
  _finish_restore "$STAGED" "$EXPECTED_ROWS" "live-snapshot"
fi

# ── the exception: restore a database from a named object ───────────────
# For the case the plan calls out: the owner's data is NOT trustworthy (a silent
# migration skip, a stalled cascade, a wrong restore) and must not be carried
# forward. The source is an explicit object a human names — the seed, or a copy
# taken deliberately — never an automatic choice among old copies.
if [ "$ACTION" = "--restore" ]; then
  SOURCE="${2:?usage: switch.sh --restore gs://<bucket>/<object>.db}"
  case "$SOURCE" in
    gs://*/*.db) ;;
    *) mark "FAILED: the restore source must be gs://<bucket>/<object>.db"; exit 1 ;;
  esac
  mark "restore: EXCEPTION PATH — this box's data comes from $SOURCE, not from a live snapshot"
  mark "restore: stopping the app (nothing writes while the DB is replaced)"
  systemctl stop houses.service 2>/dev/null || true
  STAGED="$ROOT/data/.restore-$TS.db"
  trap 'rm -f "$STAGED"' EXIT
  # gsutil verifies the CRC on transfer; retried, because a 2.3 GB fetch must not
  # fail on a transient blip.
  fetched=0
  for attempt in $(seq 1 5); do
    if gsutil -q cp "$SOURCE" "$STAGED"; then fetched=1; break; fi
    mark "restore: fetch attempt $attempt/5 failed — retrying in 15s"
    sleep 15
  done
  [ "$fetched" = 1 ] || { mark "FAILED: could not fetch $SOURCE"; exit 1; }
  _finish_restore "$STAGED" "" "$SOURCE"
fi

echo "usage: switch.sh [--snapshot | --unfreeze | --rebase [<rows>] | --restore <gs://…> | --diagnose]" >&2
exit 2
