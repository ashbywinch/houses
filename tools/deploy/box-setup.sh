#!/bin/sh
# /opt/houses box layout + units. Run as root ON THE BOX (cloud-init calls
# it after the two checkouts exist; provision.md's manual path calls it
# too — single source of truth).
#
# What it does: ACTIVE marker, deploy-script installs, chrome/browser unit,
# app units installed but DISABLED (they need /etc/houses.env first — the
# secrets cutover installs that, then `systemctl enable --now houses-blue`).
set -eu

ROOT=/opt/houses
[ "$(id -u)" = 0 ] || { echo "box-setup: run as root" >&2; exit 1; }
for side in blue green; do
  [ -d "$ROOT/$side" ] || { echo "box-setup: clone the repo to $ROOT/$side first" >&2; exit 1; }
done

mkdir -p "$ROOT/data"
echo blue > "$ROOT/ACTIVE"          # blue is live from day one
cp "$ROOT/blue/tools/deploy/run-instance.sh" "$ROOT/blue/tools/deploy/release.sh" "$ROOT/blue/tools/deploy/switch.sh" "$ROOT/blue/tools/deploy/keepalive.sh" "$ROOT/"
chmod +x "$ROOT/run-instance.sh" "$ROOT/release.sh" "$ROOT/switch.sh" "$ROOT/keepalive.sh"
chown -R ubuntu:ubuntu "$ROOT"

cp "$ROOT/blue/tools/deploy/units/"*.service /etc/systemd/system/
cp "$ROOT/blue/tools/deploy/units/"*.timer /etc/systemd/system/
systemctl daemon-reload
mkdir -p /var/lib/houses-chrome && chown ubuntu:ubuntu /var/lib/houses-chrome
systemctl enable --now houses-chrome.service
# Keep the Always-Free box above Oracle's idle-reclaim thresholds from day
# one (CPU 95th-pct <20% over 7 days = reclaimed). It tolerates a missing
# /etc/houses.env until the cutover (skips the regenerate phase, still
# exercises CPU).
systemctl enable --now houses-keepalive.timer

echo "box setup complete:"
echo "  - install /etc/houses.env (secrets cutover), then:"
echo "  - sudo systemctl enable --now houses-blue"
echo "  - verify: curl -s http://localhost:8765/health"
