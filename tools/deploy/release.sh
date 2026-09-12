#!/bin/bash
# /opt/houses/release.sh — deploy a git ref to the STANDBY, snapshot the live
# DB into the standby's smoke copy, start it, and smoke-test it. The live
# side is untouched; nothing here writes the live DB.
#
# Usage: release.sh <git-ref>      (SHA or branch/tag name)
#
# Exit non-zero on any failure so CI fails loudly. Safe to re-run: an
# interrupted release restarts from the standby state.
#
# EVERY step is mirrored to /opt/houses/logs/releases/<ts>-<ref>-<side>.log
# and to journald (tag houses-release): a failed or stalled release must be
# diagnosable from the box alone, even when the GitHub ssh dies (2026-09-07:
# a deploy hung inside the DB snapshot for 90 minutes with its output lost
# to a killed ssh — the box log now carries the evidence either way).
set -eu

# The box's provision-time stub re-execs this script with POSIX `sh`
# (`exec sh "$ROOT/$SIDE/tools/deploy/release.sh" "$REF"`) — but this
# script needs bash (process-substitution tee, local, [ -p ]). If we are
# not under bash, re-exec ourselves properly; v1.4.3 died on the box with
# `Syntax error: redirection unexpected` at line 33 otherwise.
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

ROOT="${HOUSES_ROOT:-/opt/houses}"
REF="${1:?usage: release.sh <git-ref>}"
LOG_DIR="${HOUSES_LOG_DIR:-$ROOT/logs/releases}"
mkdir -p "$LOG_DIR"

ACTIVE=$(cat "$ROOT/ACTIVE")
case "$ACTIVE" in
  blue)  SIDE=green ;;
  green) SIDE=blue ;;
  *) echo "release: bad ACTIVE marker '$ACTIVE' (expected blue|green)" >&2; exit 1 ;;
esac
PORT=8766  # the standby (this release target) always binds 8766 (role-based ports)

LOG="$LOG_DIR/$(date +%Y%m%d-%H%M%S)-${REF}-${SIDE}.log"
mark() { echo "== $(date +%H:%M:%S) $*"; logger -t houses-release "$SIDE $*"; }

# Keep the box-side transcript even if the CI ssh dies mid-release.
# POSIX-safe: the box's provision-time stub re-execs this script with `sh`,
# and dash parses the whole file before executing — process substitution
# `>(tee …)` is a PARSE ERROR under sh (v1.4.3: `Syntax error: redirection
# unexpected`). A named pipe + background tee is the sh-compatible form.
FIFO="$LOG_DIR/.tee-$$"
mkfifo "$FIFO"
tee -a "$LOG" < "$FIFO" &
TEE_PID=$!
exec > "$FIFO" 2>&1
# Arm the cleanup immediately: the FIFO/tee must be reaped even if a step
# between here and the standby logic fails. (Re-armed after the re-exec.)
trap cleanup EXIT

# Housekeeping: the release logs must not grow unbounded on the box
# (each run writes a few KB, but many failed runs of the same ref can
# accumulate).  Keep the newest 32 runs of each file wildcard, delete
# the rest.
prune_logs() {
  local keep="${1:-32}"
  find "$LOG_DIR" -maxdepth 1 -name '*.log' -type f \
    | sort -r \
    | tail -n +"$((keep + 1))" \
    | xargs -r rm -f
  mark "log housekeeping: keeping newest $keep in $LOG_DIR ($(find "$LOG_DIR" -maxdepth 1 -name '*.log' | wc -l) present)"
}

mark "release '$REF' -> $SIDE (standby; active=$ACTIVE)"

cd "$ROOT/$SIDE"
git fetch --tags --force origin
git checkout --force "$REF"
# The checkout runs as root — changed files become root-owned, and the
# app unit (ubuntu) would crash-loop on npm install EACCES.  Give the
# checkout back to ubuntu before anything runs against it.
chown -R ubuntu:ubuntu "$ROOT/$SIDE"
git rev-parse --short HEAD > "$ROOT/${SIDE}-revision"

# A release must run the ref's OWN tooling: the /opt/houses copy of this
# script is provision-time-frozen.  Re-exec the checked-out release.sh
# (checkout --force guarantees it is the authentic ref content, so a
# compromise of the ubuntu account cannot inject into it).
if [ "$0" != "$ROOT/$SIDE/tools/deploy/release.sh" ] && [ -f "$ROOT/$SIDE/tools/deploy/release.sh" ]; then
  echo "== re-exec the ref's own release.sh"
  exec sh "$ROOT/$SIDE/tools/deploy/release.sh" "$REF"
fi

# R2 — the standby is EPHEMERAL: never leave a second stack running on the
# box after a release (2026-09-07: a warm standby crash-looping on a stale
# smoke DB is what OOM-killed the 953 MiB e2-micro). The switch starts the
# new side cold, so nothing needs the standby warm.
cleanup() {
  # Reap the transcript FIFO + tee before anything else (armed early, so
  # this also covers a failure before the standby is even considered).
  rm -f "${FIFO:-}" 2>/dev/null || true
  [ -n "${TEE_PID:-}" ] && kill "$TEE_PID" 2>/dev/null || true
  # Review (PR #106): guard a bad/empty ACTIVE marker — under set -u an
  # unbound $SIDE would error inside the trap. A failed unit is inactive,
  # so is-active already covers the "nothing to stop" case.
  if [ -n "$SIDE" ] && systemctl is-active --quiet "houses-$SIDE" 2>/dev/null; then
    mark "stopping standby houses-$SIDE (ephemeral-standby policy)"
    systemctl stop "houses-$SIDE" || mark "WARNING: could not stop houses-$SIDE"
  fi
}
trap cleanup EXIT

# uv is installed per-user for ubuntu, and the venv must be ubuntu-owned
# (the app unit runs as ubuntu) — sync as ubuntu, not as the invoking root.
sudo -u ubuntu -H /home/ubuntu/.local/bin/uv sync

# R1 — CI builds the frontend and ships the tarball (HOUSES_DIST_TARBALL);
# unpack it so the standby boots with node/npm NEVER running on the box
# (a Vite build is node at ~1.3 GB total-vm — the 2026-09-07 OOM victim).
if [ -n "${HOUSES_DIST_TARBALL:-}" ]; then
  if [ ! -f "$HOUSES_DIST_TARBALL" ]; then
    mark "HOUSES_DIST_TARBALL set but '$HOUSES_DIST_TARBALL' is missing"
    exit 1
  fi
  mkdir -p "$ROOT/$SIDE/houses/frontend"
  tar -xzf "$HOUSES_DIST_TARBALL" -C "$ROOT/$SIDE/houses/frontend"
  mark "frontend dist unpacked into the standby (no on-box build)"
fi

# R6 — SHIP box tooling from the ref's own checkout. The deploy key is
# command-restricted to release.sh/switch.sh (authorized_keys), so no scp/
# install can ever reach /opt/houses or /etc/systemd — the release itself is
# the only elevated path, by design. Install the ref's copies of the units,
# watchdog, switch.sh and run-instance.sh, then reload + (re)enable.
mark "shipping box tooling (units, watchdog, switch, run-instance)"
install -m 0644 "$ROOT/$SIDE/tools/deploy/units/houses-blue.service" /etc/systemd/system/
install -m 0644 "$ROOT/$SIDE/tools/deploy/units/houses-green.service" /etc/systemd/system/
install -m 0644 "$ROOT/$SIDE/tools/deploy/units/houses-network-watchdog.service" /etc/systemd/system/
install -m 0644 "$ROOT/$SIDE/tools/deploy/units/houses-network-watchdog.timer" /etc/systemd/system/
install -m 0755 "$ROOT/$SIDE/tools/deploy/network-watchdog.sh" /opt/houses/network-watchdog.sh
install -m 0755 "$ROOT/$SIDE/tools/deploy/switch.sh" /opt/houses/switch.sh
install -m 0755 "$ROOT/$SIDE/tools/deploy/run-instance.sh" /opt/houses/run-instance.sh
systemctl daemon-reload
if grep -qi google /sys/devices/virtual/dmi/id/product_name 2>/dev/null; then
  systemctl enable --now houses-network-watchdog.timer
else
  mark "box-setup: not a GCP guest — network watchdog NOT enabled"
fi
mark "box tooling shipped (switch.sh sha: $(sha256sum /opt/houses/switch.sh | cut -c1-16))"

# R1 — the workflow pipes the CI-built dist on STDIN (scp is impossible:
# the deploy key's command= allows only release.sh/switch.sh). Read it when
# /dev/stdin is a pipe; unpack into the standby checkout so the boot skips
# any on-box build. The env-tarball path above remains for manual use.
if [ -p /dev/stdin ]; then
  mkdir -p "$ROOT/$SIDE/houses/frontend"
  tar -xzf - -C "$ROOT/$SIDE/houses/frontend"
  mark "frontend dist unpacked from stdin (no on-box build)"
fi

# Snapshot the live DB into the standby's smoke copy — sqlite .backup is
# consistent even with a live WAL writer. The standby then reads/writes its
# OWN copy; the live DB is never touched by the standby.
mark "snapshot live DB -> $SIDE smoke copy"
# Evidence first: writer activity on the live DB — the exact state that can
# stall a backup — recorded before the lock is requested.
mark "live DB state: $(ls -la "$ROOT/data/houses.db"* 2>/dev/null | tr '
' ';')"
journalctl -u "houses-$ACTIVE" --since="10 minutes ago" --no-pager 2>/dev/null | tail -15 | logger -t houses-release -s "$SIDE snapshot-writer-evidence >&2" || true
# A live writer (the eager evaluator persisting a cascade) can hold the
# lock — wait through contention instead of failing the release. The hard
# 300s cap guarantees the deploy cannot hang the CI budget silently again
# (2026-09-07: 90-min silent hang here).
# The CLI's `.backup` retries SQLITE_BUSY/LOCKED forever (its busy loop is
# NOT governed by .timeout) — the 2026-09-07 90-minute hang. Use the backup
# API through the ref's own python with a hard deadline and an explicit
# busy_timeout: the release must never wait unbounded on the live DB.  A
# PASSIVE checkpoint first (never blocks a writer) shrinks the backup window.
BACKUP_PY=$(mktemp "$ROOT/.backup-XXXXXX.py")
chown ubuntu:ubuntu "$BACKUP_PY"
cat > "$BACKUP_PY" <<'PY'
import sqlite3, sys, time
live, out = sys.argv[1], sys.argv[2]
src = sqlite3.connect(live, timeout=5)  # write-capable: a PASSIVE checkpoint must apply the WAL for the copy to include it
try:
    src.execute("PRAGMA wal_checkpoint(PASSIVE)")
except sqlite3.OperationalError:
    pass
dst = sqlite3.connect(out)
deadline = time.monotonic() + 120
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
snapshot_ok=0
for i in 1 2 3 4 5; do
  if "$ROOT/$SIDE/.venv/bin/python" "$BACKUP_PY" "$ROOT/data/houses.db" "$ROOT/$SIDE-smoke.db"; then
    snapshot_ok=1
    break
  fi
  mark "snapshot attempt $i failed or timed out (locked?) — retrying"
  sleep 10
done
rm -f "$BACKUP_PY"
if [ "$snapshot_ok" != 1 ]; then
  mark "could not snapshot the live DB — writer evidence:"
  journalctl -u "houses-$ACTIVE" --since="20 minutes ago" --no-pager 2>/dev/null | tail -40 || true
  lsof "$ROOT/data/houses.db" 2>/dev/null | head -20 || true
  exit 1
fi
chmod 600 "$ROOT/$SIDE-smoke.db"
# release.sh runs as root (sudo), but the app unit runs as ubuntu — a
# root-owned 600 file is unopenable by the standby (PR #68 review).
chown ubuntu:ubuntu "$ROOT/$SIDE-smoke.db"
mark "snapshot ok ($(du -h "$ROOT/$SIDE-smoke.db" | cut -f1))"

# R4 — pre-flight memory gate: never start the standby on a starved box
# (the 2026-09-07 OOM was a second stack starting beside an already-busy
# live side on 953 MiB). The standby needs ~300 MiB; demand 450 free.
FREE_MB=$(awk '/MemAvailable/ { print int($2 / 1024) }' /proc/meminfo)
if [ "$FREE_MB" -lt 450 ]; then
  mark "refusing to start standby: only ${FREE_MB} MiB free (need >= 450)"
  exit 1
fi
mark "memory pre-flight ok (${FREE_MB} MiB free)"

mark "restarting houses-$SIDE"
systemctl restart "houses-$SIDE"


# Wait for health on the standby port.  A first boot recomputes every
# code-stale node (minutes of cascade); since R1 the frontend is NOT built
# on the box, so the wait covers the cascade, not npm+Vite.
for i in $(seq 1 120); do
  curl -fsS --max-time 5 "localhost:$PORT/health" >/dev/null 2>&1 && break
  sleep 3
done
curl -fsS --max-time 30 "localhost:$PORT/health" >/dev/null 2>&1 || {
  echo "release: standby $SIDE not healthy on :$PORT" >&2
  systemctl status "houses-$SIDE" --no-pager | tail -20 || true
  exit 1
}
mark "standby healthy on :$PORT"

# ── authenticated smoke checks (the standby is a full prod replica) ────────
# The session secret is root-only in /etc/houses.env; mint a superuser cookie
# with the standby's own code so /api/* (auth-gated) actually executes.
SECRET=$(sudo grep '^HOUSES_SESSION_SECRET=' /etc/houses.env | head -1 | cut -d= -f2- || true)
if [ -z "$SECRET" ]; then
  echo "release: HOUSES_SESSION_SECRET missing from /etc/houses.env" >&2
  exit 1
fi
COOKIE=$(HOUSES_SESSION_SECRET="$SECRET" "$ROOT/$SIDE/.venv/bin/python" -c '
from houses.web.auth import _make_session_cookie
print(_make_session_cookie(email="simon@example.com", name="Simon", picture="", is_superuser=True))
' 2>/dev/null)

echo "== smoke: /health"
curl -fsS --max-time 180 "localhost:$PORT/health" | grep -qE '"status": ?"ok"'

echo "== smoke: /api/properties/all (>= 1 house record)"
ALL=$(curl -fsS --max-time 1800 -H "Cookie: session=$COOKIE" "localhost:$PORT/api/properties/all")
RIDS=$(echo "$ALL" | "$ROOT/$SIDE/.venv/bin/python" -c 'import json,sys; d=json.load(sys.stdin); print(len(d.get("properties", d)))' 2>/dev/null || echo 0)
echo "   house records served: $RIDS"
[ "$RIDS" -gt 0 ] || { echo "release: smoke /api/properties/all returned no house records" >&2; exit 1; }

echo "== smoke: a property detail with commutes"
RID=$(echo "$ALL" | "$ROOT/$SIDE/.venv/bin/python" -c 'import json,sys; d=json.load(sys.stdin); ps=d.get("properties", d); print(sorted(ps)[-1] if isinstance(ps, dict) else ps[0]["rid"])' 2>/dev/null || echo "")
if [ -n "$RID" ]; then
  curl -fsS --max-time 900 -H "Cookie: session=$COOKIE" "localhost:$PORT/api/properties/$RID/detail" >/dev/null
fi

echo "== smoke: frontend index (HTTP 200 + HTML)"
INDEX_FILE=$(mktemp "/tmp/houses-smoke-index-XXXXXX.html")
F_HTTP=$(curl -sS --max-time 10 -o "$INDEX_FILE" -w "%{http_code}" "localhost:$PORT/")
echo "   frontend HTTP $F_HTTP"
[ "$F_HTTP" = "200" ] || { echo "release: smoke frontend returned HTTP $F_HTTP (need 200)" >&2; rm -f "$INDEX_FILE"; exit 1; }
grep -qi "<!doctype html\|<html" "$INDEX_FILE" || { echo "release: smoke frontend returned 200 but no HTML body" >&2; rm -f "$INDEX_FILE"; exit 1; }
echo "   frontend html ok ($(wc -c < "$INDEX_FILE") bytes)"
rm -f "$INDEX_FILE"

echo "== smoke: scrape-queue health (worker liveness)"
STATUS=$(curl -fsS --max-time 10 -H "Cookie: session=$COOKIE" "localhost:$PORT/api/scrapes/status" || echo '{"scrapes":{}}')
echo "   queue: $STATUS"
PENDING=$(echo "$STATUS" | "$ROOT/$SIDE/.venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin).get("scrapes", {}).get("pending", 0))' 2>/dev/null || echo 0)
if [ "$PENDING" -gt 0 ]; then
  echo "WARNING: $PENDING scrape job(s) pending — the LAN scrape worker may be down (journalctl -u houses-scrape-worker on the LAN machine)."
fi

prune_logs
mark "release ready: smoke at http://localhost:$PORT (public: https://houses-smoke.blueumbrella.net) — standby stops on exit (the switch starts it cold)"
echo "$SIDE" > "$ROOT/SMOKE_READY"
