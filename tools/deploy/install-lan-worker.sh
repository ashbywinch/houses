#!/bin/sh
# Install the LAN scrape worker + shared headless Chrome as system services.
#
# Run ONCE with sudo on the machine that does the Rightmove scraping (the
# LAN — the cloud box has no Chrome). Installs:
#   houses-chrome.service         headless Chrome on :9222 (Restart=always)
#   houses-scrape-worker.service  polls the box's queue, scrapes, reports
# Both are boot-enabled and supervised by systemd; logs go to journald.
#
# Usage:
#   sudo HOUSES_SCRAPE_APP_URL=https://houses.blueumbrella.net \
#     bash tools/deploy/install-lan-worker.sh
#
# The repo path is derived from this script's location; the service user is
# the owner of the repo. HOUSES_SCRAPE_APP_URL defaults to the prod host.
set -eu

REPO=$(cd "$(dirname "$0")/../.." && pwd)
[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }
[ -d "$REPO/.venv" ] || { echo "no .venv at $REPO/.venv — run the repo setup first" >&2; exit 1; }
USER_NAME=$(stat -c %U "$REPO")
APP_URL=${HOUSES_SCRAPE_APP_URL:-https://houses.blueumbrella.net}

# Shared headless Chrome (the worker and the dev app both use :9222).
sed "s/^User=.*/User=$USER_NAME/" "$REPO/tools/deploy/units/houses-chrome.service" \
  > /etc/systemd/system/houses-chrome.service
mkdir -p /var/lib/houses-chrome
chown "$USER_NAME" /var/lib/houses-chrome

# The worker.
sed -e "s|__REPO__|$REPO|g" -e "s|__USER__|$USER_NAME|g" -e "s|__APP_URL__|$APP_URL|g" \
  "$REPO/tools/deploy/units/houses-scrape-worker.service" \
  > /etc/systemd/system/houses-scrape-worker.service
# A second worker drains the LAN DEV app's queue (localhost:8765) — dev
# adds would otherwise sit unclaimed (the first worker serves the box).
sed -e "s|__REPO__|$REPO|g" -e "s|__USER__|$USER_NAME|g" \
  "$REPO/tools/deploy/units/houses-scrape-worker-dev.service" \
  > /etc/systemd/system/houses-scrape-worker-dev.service

systemctl daemon-reload
systemctl enable --now houses-chrome.service houses-scrape-worker.service houses-scrape-worker-dev.service

sleep 2
systemctl is-active houses-chrome houses-scrape-worker
echo "installed: worker polls $APP_URL (logs: journalctl -u houses-scrape-worker -f)"
