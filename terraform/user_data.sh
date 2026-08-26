#!/bin/bash
# cloud-init user_data for the houses box. Runs as root at first boot.
# Installs the toolchain, then hands the layout/units to the repo's own
# box-setup.sh (single source of truth — provision.md and this file both
# call it). NO SECRETS HERE: instance metadata is readable by anyone with
# the tenancy; the .env arrives later via the cutover SSH step.
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y \
  python3-venv unzip curl ca-certificates sqlite3 rsync age rclone file \
  nodejs npm git make \
  fonts-liberation libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
  libxkbcommon0 libxcomposite1 libxdamage1 libgbm1 libasound2

# uv — install for the ubuntu service user (the app runs as ubuntu)
sudo -u ubuntu -H sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'

# Chrome for the scraper (arm64). Sanity-checked so a failed fetch can't
# silently leave the box browserless; falls back to Ubuntu's chromium.
CHROME_BIN=/usr/bin/google-chrome
curl -fL --retry 3 -o /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_arm64.deb
if file /tmp/chrome.deb | grep -q "Debian binary package"; then
  dpkg -i /tmp/chrome.deb || apt-get -f install -y
else
  echo "chrome deb unavailable — installing chromium instead"
  apt-get install -y chromium-browser
  CHROME_BIN=/usr/bin/chromium-browser
fi
"$CHROME_BIN" --headless=new --version || { echo "$CHROME_BIN does not launch headless — fix before continuing"; exit 1; }

# cloudflared for the tunnel (arm64)
curl -fL --retry 3 -o /tmp/cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
file /tmp/cloudflared.deb | grep -q "Debian binary package" || { echo "cloudflared download failed"; exit 1; }
dpkg -i /tmp/cloudflared.deb

# Two checkouts + the box layout (ACTIVE marker, units, launchers)
mkdir -p /opt/houses
chown ubuntu:ubuntu /opt/houses
cd /opt/houses
sudo -u ubuntu git clone ${repo_url} blue
sudo -u ubuntu git clone ${repo_url} green
sudo -u ubuntu bash /opt/houses/blue/tools/deploy/box-setup.sh

echo "cloud-init complete — install /etc/houses.env (cutover), then:"
echo "  systemctl enable --now houses-blue"
