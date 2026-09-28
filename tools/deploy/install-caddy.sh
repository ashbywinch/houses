#!/bin/sh
# /opt/houses/install-caddy.sh — HTTPS for the live hostname, terminated with a
# CLOUDFLARE ORIGIN CERTIFICATE.
#
# Why not Let's Encrypt: a rollout builds a FRESH box, and a fresh box has no
# certificate. Let's Encrypt would re-issue on every rollout, and its
# "5 duplicate certificates per week" limit would cap rollouts at five a week.
# Cloudflare is already in front of the origin (the A record is proxied), so the
# certificate is Cloudflare's job:
#
#   * browsers see Cloudflare's edge certificate;
#   * Cloudflare → origin uses a 15-year ORIGIN CERTIFICATE (Cloudflare's own CA)
#     that never rotates, so it can simply be fetched from the bucket on every
#     fresh box;
#   * the Cloudflare SSL/TLS mode must be **Full (strict)** — with Flexible the
#     origin leg would be plaintext (and unreachable: nothing listens on :80).
#
# Called by the box bootstrap (fresh boxes), by install-artifact.sh, and runnable
# by hand — one source of truth. The hostname comes from /etc/houses.env.
set -eu

MAIN=$(grep '^HOUSES_MAIN_HOST=' /etc/houses.env 2>/dev/null | head -1 | cut -d= -f2-)
MAIN=${MAIN:-houses.blueumbrella.net}
ORIGIN_PEM_OBJECT="${ORIGIN_PEM_OBJECT:-gs://houses-seed/cloudflare/origin.pem}"
ORIGIN_KEY_OBJECT="${ORIGIN_KEY_OBJECT:-gs://houses-seed/cloudflare/origin.key}"
CERT_DIR=/etc/caddy/certs

if ! command -v caddy >/dev/null 2>&1; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  apt-get update
  apt-get install -y caddy
fi

# The certificate pair comes from the bucket with the box's OWN identity (the
# instance service account — no key file on the box). It never expires in
# practice, so unlike a Let's Encrypt certificate it needs no renewal and no
# cache carried between boxes.
install -d -m 0755 "$CERT_DIR"
fetch_object() {
  attempt=1
  while [ "$attempt" -le 5 ]; do
    gsutil -q cp "$1" "$2" && return 0
    echo "fetch of $1 failed (attempt $attempt/5) — retrying in 15s"
    sleep 15
    attempt=$((attempt + 1))
  done
  return 1
}
fetch_object "$ORIGIN_PEM_OBJECT" "$CERT_DIR/origin.pem" || {
  echo "FAILED: no origin certificate at $ORIGIN_PEM_OBJECT" >&2
  echo "  Cloudflare dashboard → SSL/TLS → Origin Server → Create Certificate for $MAIN," >&2
  echo "  then upload the .pem and .key to gs://houses-seed/cloudflare/ (see tools/deploy/provision.md §3)." >&2
  exit 1
}
fetch_object "$ORIGIN_KEY_OBJECT" "$CERT_DIR/origin.key" || {
  echo "FAILED: no origin certificate KEY at $ORIGIN_KEY_OBJECT" >&2
  exit 1
}
chmod 644 "$CERT_DIR/origin.pem"
chown root:caddy "$CERT_DIR/origin.key"
chmod 640 "$CERT_DIR/origin.key"
openssl x509 -in "$CERT_DIR/origin.pem" -noout -subject -dates 2>/dev/null \
  || echo "WARNING: $CERT_DIR/origin.pem does not parse as a certificate"

cat > /etc/caddy/Caddyfile <<EOF
# Cloudflare terminates TLS for clients; this certificate is the Cloudflare
# ORIGIN certificate, used on the Cloudflare → origin leg (SSL/TLS mode: Full
# (strict)). Nothing here talks to an ACME CA.
$MAIN {
    tls $CERT_DIR/origin.pem $CERT_DIR/origin.key
    reverse_proxy 127.0.0.1:8765
}
EOF

systemctl enable --now caddy
# NOT `|| true`: a Caddy that will not start means nothing serves :443, and that
# must fail the install, not the settle window after traffic has moved.
systemctl restart caddy
for attempt in $(seq 1 30); do
  if curl -fsS -k --max-time 5 --resolve "$MAIN:443:127.0.0.1" "https://$MAIN/health" >/dev/null 2>&1; then
    echo "caddy installed and serving TLS: https://$MAIN -> 127.0.0.1:8765"
    exit 0
  fi
  sleep 2
done
echo "FAILED: caddy did not answer on https://$MAIN ($MAIN:443 -> 127.0.0.1)" >&2
echo "  journalctl -u caddy -n 100 --no-pager   # the certificate or the Caddyfile is rejected" >&2
exit 1
