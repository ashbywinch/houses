#!/bin/sh
# /opt/houses/install-deploy-allowlist.sh <ssh-ed25519 AAAA… comment>
#
# Writes (appends) the CORRECT command= allowlist entry to
# ~ubuntu/.ssh/authorized_keys for the restricted deploy key. Run as root
# during provisioning (box-setup.sh installs this script).
#
# WHY THIS SHAPE (2026-09-23 incident): sshd does NOT populate positional
# params in a forced command — the old provision.md entry
# `command="sudo /opt/houses/release.sh $1 …"` resolved $1 to EMPTY, so a
# fixed allowlist silently swallowed every non-matching invocation:
# `switch.sh --rollback` and `switch.sh --diagnose` NO-OPPED with exit 0
# (the 16:19 rollback never ran, and the diagnose tool was unreachable).
# OpenSSH hands the client's requested command line to the forced command
# in $SSH_ORIGINAL_COMMAND (man sshd → authorized_keys): the allowlist
# below matches on it, accepts EXACTLY the sanctioned shapes, and runs
# them. Anything else: silent no-op — and stdout/stderr are NOT silenced
# for the sanctioned shapes, so box marks stream back to the workflow.
#
# Sanctioned shapes:
#   sudo /opt/houses/release.sh <ref>         — deploy to standby (arg forwarded)
#   sudo /opt/houses/switch.sh                — flip
#   sudo /opt/houses/switch.sh --rollback     — undo last flip (arg forwarded)
#   sudo /opt/houses/switch.sh --diagnose     — read-only box state dump
#   sudo journalctl <options>                 — read-only logs
#
# Usage: install-deploy-allowlist.sh "$(cat deploy.pub)"
set -eu

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
PUBKEY="${1:?usage: install-deploy-allowlist.sh <pubkey>}"
case "$PUBKEY" in
  ssh-ed25519\ *|ssh-rsa\ *) : ;;
  *) echo "pubkey does not look like an OpenSSH public key" >&2; exit 1 ;;
esac

HOMEDIR=$(getent passwd ubuntu | cut -d: -f6)
SSHDIR="$HOMEDIR/.ssh"
mkdir -p "$SSHDIR"
chmod 700 "$SSHDIR"

ENTRY=$(cat <<ENTRY
command="/opt/houses/deploy-allowlist.sh",no-pty,no-agent-forwarding,no-port-forwarding $PUBKEY
ENTRY
)

# Replace any previous entry for the same comment tag (idempotent re-run).
AUTHORIZED="$SSHDIR/authorized_keys"
TMP="$SSHDIR/.authorized_keys.tmp"
grep -v '# houses-deploy-allowlist' "$AUTHORIZED" 2>/dev/null > "$TMP" || true
cat >> "$TMP" <<ENTRY
# houses-deploy-allowlist
$(cat <<E2
command="/opt/houses/deploy-allowlist.sh",no-pty,no-agent-forwarding,no-port-forwarding $PUBKEY
E2
)
ENTRY
mv "$TMP" "$AUTHORIZED"
chmod 600 "$AUTHORIZED"
chown ubuntu:ubuntu "$SSHDIR" "$AUTHORIZED"
echo "allowlist installed for $PUBKEY"