#!/bin/bash
# tools/deploy/provision-box.sh — render cloud-init userdata for a fresh
# houses box. Run by the Release workflow's `provision` action; provides
# the whole box as code: packages, checkouts, layout/units, deploy-key
# allowlist, operator key, off-box data+env restore, Cloudflare tunnel,
# backup units.
#
# The secrets are baked into the userdata and therefore live in Oracle
# instance metadata (readable only from inside the instance). The
# bootstrap shreds its own copy after running.
#
# usage:
#   PROVISION_REF=main \
#   DEPLOY_PUBKEY='ssh-ed25519 AAAA… deploy@houses' \
#   OPERATOR_PUBKEY='ssh-ed25519 AAAA… operator' \
#   AGE_KEY='AGE-SECRET-KEY-…' \
#   AGE_RECIPIENT='age1…' \
#   CF_TUNNEL_TOKEN='…' \
#   RCLONE_CONFIG="type = s3\nprovider = …\naccess_key_id = …" \
#   tools/deploy/provision-box.sh > /tmp/houses-userdata.sh
set -euo pipefail

: "${PROVISION_REF:?} ${DEPLOY_PUBKEY:?} ${OPERATOR_PUBKEY:?} ${AGE_KEY:?} ${AGE_RECIPIENT:?} ${CF_TUNNEL_TOKEN:?} ${RCLONE_CONFIG:?}"

# single-quote escaping so any byte value survives the wrapper statements
sq() { printf '%s' "$1" | sed "s/'/'\"'\"'/g"; }

BOOTSTRAP="$(cat "$(dirname "$0")/box-bootstrap.sh")"

cat <<WRAPPER
#!/bin/bash
# auto-generated userdata from tools/deploy/provision-box.sh (ref: $PROVISION_REF)
set -euo pipefail
export PROVISION_REF='$(sq "$PROVISION_REF")'
export DEPLOY_PUBKEY='$(sq "$DEPLOY_PUBKEY")'
export OPERATOR_PUBKEY='$(sq "$OPERATOR_PUBKEY")'
export AGE_KEY='$(sq "$AGE_KEY")'
export AGE_RECIPIENT='$(sq "$AGE_RECIPIENT")'
export CF_TUNNEL_TOKEN='$(sq "$CF_TUNNEL_TOKEN")'
export RCLONE_CONFIG='$(sq "$RCLONE_CONFIG")'
cat > /tmp/box-bootstrap.sh <<'BOOTSTRAP_EOF'
$BOOTSTRAP
BOOTSTRAP_EOF
chmod 700 /tmp/box-bootstrap.sh
bash /tmp/box-bootstrap.sh
rc=\$?
rm -f /tmp/box-bootstrap.sh   # shred the secrets after the build
exit \$rc
WRAPPER