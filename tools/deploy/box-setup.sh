#!/bin/sh
# /opt/houses box layout + units. Run as root ON THE BOX (cloud-init calls
# it after the two checkouts exist; provision.md's manual path calls it
# too — single source of truth). IDEMPOTENT: safe to re-run on a fresh
# instance or over an existing install (a rebuild is a re-run, not surgery).
#
# DOCTRINE (2026-09-23, after the frozen-bootstrap incident):
#   * Replace-not-repair. The box is disposable; recovery = reimage from
#     this script + restore the off-box backup, never ad-hoc fixes.
#   * No frozen bootstrap. /opt/houses/release.sh is refreshed from the
#     ref on EVERY release (R6 self-ship) and calls uv by absolute path —
#     a broken bootstrap can never deadlock the box again.
#   * Restricted SSH with a WORKING allowlist. The deploy key matches on
#     $SSH_ORIGINAL_COMMAND (sshd does NOT populate "$1" in forced
#     commands); sanctioned shapes = release.sh <ref>, switch.sh
#     [--rollback|--diagnose], journalctl (read-only). /opt/houses/
#     install-deploy-allowlist.sh writes that entry from a given pubkey.
#   * Human prod gate on GitHub (Environments → Required reviewers), not
#     on the box.
#   * Break-glass = OCI serial console (works with keys/units broken).
set -eu

ROOT=/opt/houses
[ "$(id -u)" = 0 ] || { echo "box-setup: run as root" >&2; exit 1; }
for side in blue green; do
  [ -d "$ROOT/$side" ] || { echo "box-setup: clone the repo to $ROOT/$side first" >&2; exit 1; }
done

# blue is live from day one; never clobber an existing marker (idempotent)
[ -f "$ROOT/ACTIVE" ] || echo blue > "$ROOT/ACTIVE"

# Tooling: install the CURRENT copies (box-setup runs from the checkout,
# so a rebuild gets the survivor release.sh; per-release self-ship keeps
# it fresh afterwards).
install -m 0755 "$ROOT/blue/tools/deploy/run-instance.sh" "$ROOT/blue/tools/deploy/release.sh" "$ROOT/blue/tools/deploy/switch.sh" "$ROOT/blue/tools/deploy/run-migration.sh" "$ROOT/blue/tools/deploy/deploy-allowlist.sh" "$ROOT/blue/tools/deploy/install-deploy-allowlist.sh" "$ROOT/"
chown -R ubuntu:ubuntu "$ROOT"
# The deploy scripts execute as root via the sudoers rule — they must NOT
# be writable by the sudo-able user, or any ubuntu compromise could edit
# a script and escalate to root, defeating the guard (PR #68 security
# review).
chown root:root "$ROOT/run-instance.sh" "$ROOT/release.sh" "$ROOT/switch.sh" "$ROOT/run-migration.sh" "$ROOT/deploy-allowlist.sh" "$ROOT/install-deploy-allowlist.sh"
chmod 755 "$ROOT/run-instance.sh" "$ROOT/release.sh" "$ROOT/switch.sh" "$ROOT/run-migration.sh" "$ROOT/deploy-allowlist.sh" "$ROOT/install-deploy-allowlist.sh"

install -m 0644 "$ROOT/blue/tools/deploy/migrations.list" "$ROOT/migrations.list"
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
echo "  - run-instance/release/switch/run-migration installed (root-owned, current ref)"
echo "  - units + sudoers installed (journalctl read-only allowed)"
echo "  - next: ./install-deploy-allowlist.sh <pubkey>  (writes the SSH allowlist)"
echo "  - break-glass: OCI serial console — reimage + box-setup + restore backup"