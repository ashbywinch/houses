#!/bin/sh
# /opt/houses/network-watchdog.sh — reboot a guest whose network has died.
#
# 2026-09-08: memory pressure took this guest's networking (the OSConfigAgent
# could not reach the metadata server); nothing recovered it, and prod was
# unreachable for four days while the VM reported RUNNING. The metadata server
# is the cheapest guest-level network proof (link-local, always reachable on a
# healthy GCP guest).
#
# Exit 0 on a healthy check; after HOUSES_WATCHDOG_TRIES consecutive misses,
# reboot. systemd's timer drives the cadence (see houses-network-watchdog.timer).
set -u

TRIES="${HOUSES_WATCHDOG_TRIES:-3}"
SLEEP="${HOUSES_WATCHDOG_SLEEP:-20}"

i=0
while [ "$i" -lt "$TRIES" ]; do
  if curl -fsS --max-time 5 -H "Metadata-Flavor: Google" \
      http://169.254.169.254/computeMetadata/v1/ >/dev/null 2>&1; then
    exit 0
  fi
  i=$((i + 1))
  [ "$i" -lt "$TRIES" ] && sleep "$SLEEP"
done

logger -t houses-watchdog "metadata server unreachable ${TRIES}x — rebooting to restore networking"
systemctl reboot
exit 0