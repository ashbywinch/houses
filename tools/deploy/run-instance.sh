#!/bin/sh
# /opt/houses/run-instance.sh — launcher for one houses instance (blue|green).
#
# Blue/green model: the ACTIVE side (per /opt/houses/ACTIVE) serves the LIVE
# database. The other side, when started, serves its own snapshot copy
# (/opt/houses/<side>-smoke.db, taken by release.sh) — a fully working prod
# replica whose writes land in the copy, never the live DB. That is the
# pre-switch smoke target.
#
# Ports are FIXED per side (blue=8765, green=8766); the public hostnames are
# mapped to those ports by the Cloudflare tunnel (switch.sh swaps the
# mapping on flip). Env for both sides comes from /etc/houses.env; this
# script overrides the per-instance bits (port, DB path, host).
set -eu

SIDE="${1:?usage: run-instance.sh <blue|green>}"
ROOT="${HOUSES_ROOT:-/opt/houses}"
LIVE_DB="$ROOT/data/houses.db"
SMOKE_DB="$ROOT/${SIDE}-smoke.db"
ACTIVE_FILE="$ROOT/ACTIVE"

case "$SIDE" in
  blue)  PORT=8765 ;;
  green) PORT=8766 ;;
  *) echo "usage: run-instance.sh <blue|green>" >&2; exit 1 ;;
esac

# A missing ACTIVE file means the box is not yet provisioned — treat as smoke
# so a stray start can never touch a live DB it shouldn't.
if [ -f "$ACTIVE_FILE" ] && [ "$(cat "$ACTIVE_FILE")" = "$SIDE" ]; then
  DB="$LIVE_DB"
else
  DB="$SMOKE_DB"
fi

cd "$ROOT/$SIDE"
# systemd's default PATH lacks ~/.local/bin (uv) — make's UV fallback handles
# it, but be explicit so make/npm resolve identically to a shell.
export PATH="$HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export HOUSES_HOST=0.0.0.0
export HOUSES_PORT="$PORT"
export HOUSES_SQLITE_PATH="$DB"

# Wait for the scraper browser's CDP endpoint before serving (the app can
# start either way, but property adds need it live) — and fail loudly if it
# never comes up.
for i in $(seq 1 30); do
  curl -fsS --max-time 3 localhost:9222/json/version >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS --max-time 3 localhost:9222/json/version >/dev/null 2>&1 || {
  echo "scraper browser not reachable on :9222" >&2
  exit 1
}

exec make run-prod
