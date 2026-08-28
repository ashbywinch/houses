#!/bin/sh
# /opt/houses/install-caddy.sh — HTTPS for the blue/green hostnames.
#
# The only reason the deploy needs HTTPS is Google OAuth (public-host
# redirect URIs must be https://). Caddy terminates TLS on the box with
# automatic Let's Encrypt certs; the Caddyfile is STATIC because ports
# are role-based: the ACTIVE side always binds 8765, the standby 8766.
#
# Called by the terraform startup script (fresh boxes) and runnable by
# hand on an existing box — one source of truth. Hostnames come from
# /etc/houses.env (HOUSES_MAIN_HOST / HOUSES_SMOKE_HOST) or defaults.
set -eu

MAIN=$(grep '^HOUSES_MAIN_HOST=' /etc/houses.env 2>/dev/null | head -1 | cut -d= -f2-)
SMOKE=$(grep '^HOUSES_SMOKE_HOST=' /etc/houses.env 2>/dev/null | head -1 | cut -d= -f2-)
MAIN=${MAIN:-houses.blueumbrella.net}
SMOKE=${SMOKE:-houses-smoke.blueumbrella.net}

if ! command -v caddy >/dev/null 2>&1; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  apt-get update
  apt-get install -y caddy
fi

cat > /etc/caddy/Caddyfile <<EOF
# houses blue/green — ports are role-based (active=8765, standby=8766),
# so this file never changes on a flip.
$MAIN {
    reverse_proxy 127.0.0.1:8765
}

$SMOKE {
    reverse_proxy 127.0.0.1:8766
}
EOF

systemctl enable --now caddy
echo "caddy installed: https://$MAIN -> :8765 (active), https://$SMOKE -> :8766 (standby)"
