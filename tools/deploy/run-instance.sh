#!/bin/sh
# /opt/houses/run-instance.sh — launcher for THIS box's app (port 8765).
#
# One box = one app = one port. The box's role is decided by the L4 forwarding
# rule's target, not by this script: whichever instance is the target receives
# 80/443 and serves houses.blueumbrella.net; the other serves nothing external
# and its smoke runs on 127.0.0.1. There is no ACTIVE marker, no per-side port
# and no smoke copy any more (docs/anti-fragile-rollout-plan.md, Phase 2).
#
# The database at $ROOT/data/houses.db is this box's own copy — the owner's live
# DB, or the standby's copy of it from the last cutover's restore.
set -eu

ROOT="${HOUSES_ROOT:-/opt/houses}"
APP="$ROOT/app"

# The artifact's venv is the source of truth: the prod boot path runs NO uv sync
# and needs NO network (2026-09-24: a box whose venv was never installed served
# nothing).
[ -x "$APP/.venv/bin/python" ] || { echo "run-instance: no venv at $APP/.venv — install the artifact first" >&2; exit 1; }

cd "$APP"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export HOUSES_HOST=0.0.0.0
export HOUSES_PORT=8765
export HOUSES_SQLITE_PATH="$ROOT/data/houses.db"

# Wait for the scraper browser's CDP endpoint before serving — but only when a
# browser is installed on this box. The GCP host has no Chrome (the scraper
# lives on the LAN); the wait must not block startup there.
if command -v google-chrome >/dev/null 2>&1 || command -v chromium-browser >/dev/null 2>&1; then
  for i in $(seq 1 30); do
    curl -fsS --max-time 3 localhost:9222/json/version >/dev/null 2>&1 && break
    sleep 1
  done
  curl -fsS --max-time 3 localhost:9222/json/version >/dev/null 2>&1 || {
    echo "scraper browser not reachable on :9222" >&2
    exit 1
  }
fi

# serve_prod.py mounts the shipped frontend build and runs uvicorn — no make,
# no uv, no on-box build.
exec "$APP/.venv/bin/python" "$APP/serve_prod.py"
