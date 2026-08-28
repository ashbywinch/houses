#!/bin/bash
# startup-script (cloud-init) for the houses box. Runs as root at first boot.
# Installs the toolchain, then hands the layout/units to the repo's own
# box-setup.sh (single source of truth). NO SECRETS HERE: instance metadata
# is readable by anyone with the project; the .env arrives later via the
# cutover SSH step.
#
# No Chrome: the Rightmove scraper lives on the LAN (the box enqueues
# scrape jobs — houses/scrape_queue.py — and the LAN worker completes them).
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y \
  python3-venv unzip curl ca-certificates sqlite3 rsync age rclone file \
  nodejs npm git make \
  fonts-liberation

# uv — install for the ubuntu service user (the app runs as ubuntu)
sudo -u ubuntu -H sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'

# Two checkouts + the box layout (ACTIVE marker, units, launchers)
mkdir -p /opt/houses
chown ubuntu:ubuntu /opt/houses
cd /opt/houses
sudo -u ubuntu git clone -b ${repo_branch} ${repo_url} blue
sudo -u ubuntu git clone -b ${repo_branch} ${repo_url} green

# Caddy for HTTPS (Google OAuth needs public-host https redirect URIs);
# reverse-proxies the role-based ports. Needs apt deps for the repo; the
# startup script runs as root, and install-caddy needs root too.
apt-get install -y gpg debian-archive-keyring
bash /opt/houses/blue/tools/deploy/install-caddy.sh


# box-setup must run AS ROOT (installs systemd units, enables chrome); the
# startup script already runs as root — no sudo -u ubuntu here.
bash /opt/houses/blue/tools/deploy/box-setup.sh
echo "  systemctl enable --now houses-blue"
