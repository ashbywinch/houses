#!/bin/sh
# /opt/houses/release.sh — deploy a git ref to the STANDBY, snapshot the live
# DB into the standby's smoke copy, start it, and smoke-test it. The live
# side is untouched; nothing here writes the live DB.
#
# Usage: release.sh <git-ref>      (SHA or branch/tag name)
#
# Exit non-zero on any failure so CI fails loudly. Safe to re-run: an
# interrupted release restarts from the standby state.
set -eu

ROOT="${HOUSES_ROOT:-/opt/houses}"
REF="${1:?usage: release.sh <git-ref>}"

ACTIVE=$(cat "$ROOT/ACTIVE")
case "$ACTIVE" in
  blue)  SIDE=green ;;
  green) SIDE=blue ;;
  *) echo "release: bad ACTIVE marker '$ACTIVE' (expected blue|green)" >&2; exit 1 ;;
esac
PORT=8766  # the standby (this release target) always binds 8766 (role-based ports)

echo "== release '$REF' -> $SIDE (standby; active=$ACTIVE)"

cd "$ROOT/$SIDE"
git fetch origin --tags
git checkout --force "$REF"
git rev-parse --short HEAD > "$ROOT/${SIDE}-revision"

uv sync --all-extras

# Snapshot the live DB into the standby's smoke copy — sqlite .backup is
# consistent even with a live WAL writer. The standby then reads/writes its
# OWN copy; the live DB is never touched by the standby.
echo "== snapshot live DB -> $SIDE smoke copy"
sqlite3 "$ROOT/data/houses.db" ".backup '$ROOT/$SIDE-smoke.db'"
chmod 600 "$ROOT/$SIDE-smoke.db"
# release.sh runs as root (sudo), but the app unit runs as ubuntu — a
# root-owned 600 file is unopenable by the standby (PR #68 review).
chown ubuntu:ubuntu "$ROOT/$SIDE-smoke.db"

systemctl restart "houses-$SIDE"

# Wait for health on the standby port.
for i in $(seq 1 60); do
  curl -fsS --max-time 3 "localhost:$PORT/health" >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS --max-time 3 "localhost:$PORT/health" >/dev/null 2>&1 || {
  echo "release: standby $SIDE not healthy on :$PORT" >&2
  systemctl status "houses-$SIDE" --no-pager | tail -20 || true
  exit 1
}
echo "== standby healthy on :$PORT"

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
curl -fsS --max-time 5 "localhost:$PORT/health" | grep -q '"status": "ok"'

echo "== smoke: /api/properties/all"
ALL=$(curl -fsS --max-time 30 -H "Cookie: session=$COOKIE" "localhost:$PORT/api/properties/all")
RIDS=$(echo "$ALL" | "$ROOT/$SIDE/.venv/bin/python" -c 'import json,sys; d=json.load(sys.stdin); print(len(d.get("properties", d)))' 2>/dev/null || echo 0)
echo "   properties served: $RIDS"
[ "$RIDS" -gt 0 ] || { echo "release: smoke /api/properties/all returned no properties" >&2; exit 1; }

echo "== smoke: a property detail with commutes"
RID=$(echo "$ALL" | "$ROOT/$SIDE/.venv/bin/python" -c 'import json,sys; d=json.load(sys.stdin); ps=d.get("properties", d); print(sorted(ps)[-1] if isinstance(ps, dict) else ps[0]["rid"])' 2>/dev/null || echo "")
if [ -n "$RID" ]; then
  curl -fsS --max-time 60 -H "Cookie: session=$COOKIE" "localhost:$PORT/api/properties/$RID/detail" >/dev/null
fi

echo "== smoke: frontend index"
curl -fsS --max-time 10 "localhost:$PORT/" | grep -qi "<!doctype html\|<html"

echo "== smoke: scrape-queue health (worker liveness)"
STATUS=$(curl -fsS --max-time 10 -H "Cookie: session=$COOKIE" "localhost:$PORT/api/scrapes/status" || echo '{"scrapes":{}}')
echo "   queue: $STATUS"
PENDING=$(echo "$STATUS" | "$ROOT/$SIDE/.venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin).get("scrapes", {}).get("pending", 0))' 2>/dev/null || echo 0)
if [ "$PENDING" -gt 0 ]; then
  echo "WARNING: $PENDING scrape job(s) pending — the LAN scrape worker may be down (journalctl -u houses-scrape-worker on the LAN machine)."
fi

echo "== release ready: smoke at http://localhost:$PORT (public: https://houses-smoke.blueumbrella.net)"
echo "$SIDE" > "$ROOT/SMOKE_READY"
