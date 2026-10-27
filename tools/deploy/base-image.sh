#!/bin/bash
# tools/deploy/base-image.sh — the BAKE script for the box base machine image.
#
# Run as the `base_builder` instance's startup-script by terraform/main.tf, which
# then snapshots that disk into the `houses-base` image every box is created
# from. It exists so a fresh box per rollout does NOT depend on the apt mirrors
# (or on the Caddy apt repo) at rollout time — those are the slowest and least
# reliable minutes of a bootstrap, and they change under us daily.
#
# WHAT BELONGS HERE: the whole OS layer — every package the box needs, the Caddy
# binary, the directory layout, and nothing belonging to the app. NO app, NO
# code, NO secret, NO database: the artifact supplies the app and its venv.
#
# The bootstrap VERIFIES this layer and refuses to run without it; it never
# installs. If a package is needed on the box, add it HERE and re-bake —
# installing it in box-bootstrap.sh would make the image non-load-bearing and
# charge every rollout the apt cost (2026-10-27: a bake that skipped
# verification cost 120.7s per provision while looking like it worked).
set -euo pipefail

echo "== base image bake: $(date -u +%FT%TZ)"

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  sqlite3 curl ca-certificates gnupg debian-archive-keyring openssh-client >/dev/null

# gsutil: the box's only route to the bucket (artifact, seed, env, origin cert).
if ! command -v gsutil >/dev/null 2>&1; then
  apt-get install -y -qq google-cloud-cli >/dev/null
fi

# Caddy from its own apt repo, so the rollout's install-caddy.sh finds the binary
# already present and skips the repo setup entirely.
if ! command -v caddy >/dev/null 2>&1; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  apt-get update -qq
  apt-get install -y -qq caddy >/dev/null
fi

# Layout the bootstrap and the app expect. Ownership matters: the app runs as
# ubuntu, and Caddy's TLS job dies on its own storage check when the package's
# root-owned directory is left as-is (2026-09-24).
install -d -o ubuntu -g ubuntu /opt/houses /opt/houses/data /opt/houses/logs/releases
install -d -m 0755 /etc/caddy/certs
chown -R caddy:caddy /var/lib/caddy 2>/dev/null || true

# VERIFY before powering off. The bootstrap refuses to run on an image missing
# any of this, so a bake that silently skipped an install would produce an image
# that fails every rollout — and the failure would look like a bootstrap bug.
# A bake must fail HERE instead, while its own log is the thing being read.
missing=""
for tool in sqlite3 curl gpg gsutil caddy; do
  command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"
done
for dir in /opt/houses/data /etc/caddy/certs; do
  [ -d "$dir" ] || missing="$missing $dir"
done
if [ -n "$missing" ]; then
  echo "== BAKE FAILED: the OS layer is incomplete:$missing" >&2
  echo "== fix tools/deploy/base-image.sh and re-bake; do NOT leave this image" >&2
  # Leave the instance RUNNING so its log is readable — the bake workflow
  # treats a non-TERMINATED builder as a failed bake and refuses to image it.
  exit 1
fi
echo "== OS layer verified: sqlite3 curl gpg gsutil caddy; /opt/houses/data /etc/caddy/certs"

# Clean up after ourselves: the image must not carry apt's transient state.
apt-get clean
rm -rf /var/lib/apt/lists/*

printf 'baked_at=%s script_sha256=%s\n' "$(date -u +%FT%TZ)" \
  "$(sha256sum "$0" 2>/dev/null | cut -d' ' -f1)" > /opt/houses/BASE-IMAGE
chmod 644 /opt/houses/BASE-IMAGE

echo "== base image bake complete: $(cat /opt/houses/BASE-IMAGE)"

# POWER OFF — this IS the completion signal. The bake workflow waits for the
# instance to reach TERMINATED and only then takes the image from its disk, so a
# truncated bake can never be mistaken for a good one (terraform cannot observe a
# startup script's progress, and stopping the box on creation would cut the bake
# short mid-apt).
sync
shutdown -h now '' || poweroff
