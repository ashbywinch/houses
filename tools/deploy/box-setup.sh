#!/bin/sh
# /opt/houses box layout + units. Run as root ON THE BOX (the bootstrap calls it
# after the artifact is unpacked; install-artifact.sh calls it on every rollout,
# so the layout is refreshed from the artifact rather than repaired by hand).
#
# ONE checkout (/opt/houses/app), ONE unit (houses.service, port 8765). Roles
# rotate with the L4 forwarding rule's target — there is no blue/green, no
# ACTIVE/PREVIOUS, no per-side smoke DB and no per-side port
# (docs/anti-fragile-rollout-plan.md, Phase 2).
#
# DOCTRINE:
#   * Replace-not-repair. The box is disposable; recovery = reimage + install
#     the artifact, never ad-hoc fixes.
#   * No frozen bootstrap. /opt/houses/install-artifact.sh (and every other
#     box script) is re-shipped from the artifact on every rollout.
#   * Restricted SSH with a WORKING allowlist: the deploy key matches on
#     $SSH_ORIGINAL_COMMAND (sshd does NOT populate "$1" in forced commands);
#     sanctioned shapes = install-artifact.sh <object>, switch.sh
#     --snapshot|--rebase|--diagnose, journalctl (read-only).
#   * Human prod gate on GitHub (Environments → Required reviewers), not on the
#     box.
#   * Break-glass = the operator key (shell + sudo) or the GCP serial console.
set -eu

ROOT=/opt/houses
APP="$ROOT/app"
[ "$(id -u)" = 0 ] || { echo "box-setup: run as root" >&2; exit 1; }
[ -d "$APP" ] || { echo "box-setup: unpack the artifact to $APP first (install-artifact.sh)" >&2; exit 1; }
SRC="${HOUSES_ARTIFACT_TREE:-$APP}"
# ^ the tooling source tree: the packaged checkout by default; a tooling-only
#   sync (install-artifact.sh --tooling-only) points it at the STAGED artifact,
#   so a box whose app is older still installs the NEW tooling.
[ -d "$SRC" ] || { echo "box-setup: no tooling source at $SRC" >&2; exit 1; }

install -d -o ubuntu -g ubuntu "$ROOT/data" "$ROOT/logs/releases"

# Tooling: the CURRENT copies, from the artifact's own checkout. The app runs as
# ubuntu, so the checkout itself is ubuntu-owned.
install -m 0755 "$SRC/tools/deploy/run-instance.sh" "$SRC/tools/deploy/switch.sh" "$SRC/tools/deploy/run_migrations.py" "$SRC/tools/deploy/install-artifact.sh" "$SRC/tools/deploy/deploy-allowlist.sh" "$SRC/tools/deploy/install-deploy-allowlist.sh" "$ROOT/"
install -m 0644 "$SRC/tools/deploy/migrations.list" "$ROOT/migrations.list"
chown -R ubuntu:ubuntu "$APP"
# The deploy scripts execute as root via the sudoers rule — they must NOT be
# writable by the sudo-able user, or any ubuntu compromise could edit a script
# and escalate to root, defeating the guard (PR #68 security review).
chown root:root "$ROOT/run-instance.sh" "$ROOT/switch.sh" "$ROOT/run_migrations.py" "$ROOT/install-artifact.sh" "$ROOT/deploy-allowlist.sh" "$ROOT/install-deploy-allowlist.sh"
chmod 755 "$ROOT/run-instance.sh" "$ROOT/switch.sh" "$ROOT/run_migrations.py" "$ROOT/install-artifact.sh" "$ROOT/deploy-allowlist.sh" "$ROOT/install-deploy-allowlist.sh"

# ONE unit. The per-side units are gone — delete them here as well so a box that
# was once blue/green cannot keep a second stack alive across a rollout.
install -m 0644 "$SRC/tools/deploy/units/houses.service" /etc/systemd/system/
rm -f /etc/systemd/system/houses-blue.service /etc/systemd/system/houses-green.service
systemctl daemon-reload
# Enabled (a reboot must bring the app back on the owner), NOT started: the
# rollout decides when the app runs, and the install's smoke stops it again.
systemctl enable houses.service >/dev/null 2>&1 || true

cp "$SRC/tools/deploy/units/houses-network-watchdog.service" /etc/systemd/system/
cp "$SRC/tools/deploy/units/houses-network-watchdog.timer" /etc/systemd/system/
install -m 0755 "$SRC/tools/deploy/network-watchdog.sh" "$ROOT/network-watchdog.sh"
systemctl daemon-reload
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
# The scraper lives on the LAN; the box has no Chrome. Enable the shared chrome
# unit only when a browser binary is actually installed.
if command -v google-chrome >/dev/null 2>&1 || command -v chromium-browser >/dev/null 2>&1; then
  systemctl enable --now houses-chrome.service
fi

# Production guard: the ONLY elevated app operations are these scripts. Remove
# ubuntu from the sudo/google-sudoers groups and install a restricted sudoers
# file — no interactive login (or agent session) can restart app units or mutate
# the deployment directly; that is the rollout's job. journalctl stays for
# read-only diagnostics.
for g in sudo google-sudoers; do
  gpasswd -d ubuntu "$g" >/dev/null 2>&1 || true
done
cat > /etc/sudoers.d/houses-deploy <<'SUDOERS'
ubuntu ALL=(root) NOPASSWD: /opt/houses/install-artifact.sh *
ubuntu ALL=(root) NOPASSWD: /opt/houses/switch.sh *
ubuntu ALL=(root) NOPASSWD: /usr/bin/journalctl *
SUDOERS
chmod 440 /etc/sudoers.d/houses-deploy
visudo -c >/dev/null

echo "box setup complete:"
echo "  - one checkout ($APP), one unit (houses.service, port 8765)"
echo "  - tooling installed root-owned from the artifact"
echo "  - sudoers: install-artifact.sh, switch.sh, journalctl (read-only)"
echo "  - next: ./install-deploy-allowlist.sh <pubkey>  (writes the SSH allowlist)"
