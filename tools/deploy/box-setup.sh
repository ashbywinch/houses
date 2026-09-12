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
cp "$ROOT/blue/tools/deploy/run-instance.sh" "$ROOT/blue/tools/deploy/release.sh" "$ROOT/blue/tools/deploy/switch.sh" "$ROOT/"
chmod +x "$ROOT/run-instance.sh" "$ROOT/release.sh" "$ROOT/switch.sh"
chown -R ubuntu:ubuntu "$ROOT"
# The deploy scripts execute as root via the sudoers rule — they must NOT
# be writable by the sudo-able user, or any ubuntu compromise could edit
# a script and escalate to root, defeating the guard (PR #68 security
# review).
chown root:root "$ROOT/run-instance.sh" "$ROOT/release.sh" "$ROOT/switch.sh"
chmod 755 "$ROOT/run-instance.sh" "$ROOT/release.sh" "$ROOT/switch.sh"
cp "$ROOT/blue/tools/deploy/units/"*.service /etc/systemd/system/
systemctl daemon-reload
cp "$ROOT/blue/tools/deploy/units/"*.timer /etc/systemd/system/
cp "$ROOT/blue/tools/deploy/network-watchdog.sh" /opt/houses/network-watchdog.sh
chmod 755 /opt/houses/network-watchdog.sh
# R5 — the guest network watchdog (reboot a guest whose network died, as on
# 2026-09-08: four days unreachable with the VM running). Installed for all
# boxes; the GCP metadata check on a non-GCP box would fail and reboot it —
# SKIP unless this is a GCP guest.
if [ -d /sys/devices/virtual/dmi/id ] && grep -qi google /sys/devices/virtual/dmi/id/product_name 2>/dev/null; then
  systemctl enable --now houses-network-watchdog.timer
else
  echo "box-setup: not a GCP guest — network watchdog NOT enabled"
fi
mkdir -p /var/lib/houses-chrome && chown ubuntu:ubuntu /var/lib/houses-chrome
# The scraper lives on the LAN; the box has no Chrome. Enable the shared
# chrome unit only when a browser binary is actually installed (the LAN
# dev machine, or a fallback VPS that does host the scraper).
if command -v google-chrome >/dev/null 2>&1 || command -v chromium-browser >/dev/null 2>&1; then
  systemctl enable --now houses-chrome.service
fi

# Production guard: the ONLY elevated app operations are the release
# scripts. Remove ubuntu from the sudo/google-sudoers groups and install a
# restricted sudoers file — no interactive login (or agent session) can
# restart app units or mutate the deployment directly; that is the
# release process's job. journalctl stays for read-only diagnostics.
for g in sudo google-sudoers; do
  gpasswd -d ubuntu "$g" >/dev/null 2>&1 || true
done
cat > /etc/sudoers.d/houses-deploy <<'SUDOERS'
ubuntu ALL=(root) NOPASSWD: /opt/houses/release.sh *
ubuntu ALL=(root) NOPASSWD: /opt/houses/switch.sh *
ubuntu ALL=(root) NOPASSWD: /usr/bin/journalctl *
SUDOERS
chmod 440 /etc/sudoers.d/houses-deploy
visudo -c >/dev/null

echo "box setup complete:"
