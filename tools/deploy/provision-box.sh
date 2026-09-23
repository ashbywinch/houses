#!/bin/bash
# tools/deploy/provision-box.sh — render the GCP startup-script for a
# fresh houses box. Run by the Release workflow's `provision` action;
# provides the whole box as code: packages, checkouts, layout/units, SSH
# paths (operator key + allowlisted deploy key — installed FIRST), Caddy
# HTTPS (:443, the box's own proven path — there is NO tunnel), and the
# data seed restore from GCS (the migrated DB from the seed action).
#
# The bootstrap inputs land in GCP instance metadata (readable only
# inside the project). The one secret it carries is the SEED_SA_KEY
# (base64 service-account JSON for gsutil).
#
# usage:
#   PROVISION_REF=main \
#   DEPLOY_PUBKEY='ssh-ed25519 AAAA… deploy@houses' \
#   OPERATOR_PUBKEY='ssh-ed25519 AAAA… operator' \
#   SEED_SA_KEY='base64…' SEED_PROJECT='houses-498215' GCS_SEED='gs://houses-seed/latest.db' \
#   tools/deploy/provision-box.sh > /tmp/houses-startup.sh
set -euo pipefail

: "${PROVISION_REF:?}" "${DEPLOY_PUBKEY:?}" "${OPERATOR_PUBKEY:?}"
SEED_SA_KEY="${SEED_SA_KEY:-}"
SEED_PROJECT="${SEED_PROJECT:-houses-498215}"
GCS_SEED="${GCS_SEED:-gs://houses-seed/latest.db}"

# single-quote escaping so any byte value survives the wrapper statements
sq() { printf '%s' "$1" | sed "s/'/'\"'\"'/g"; }

BOOTSTRAP="$(cat "$(dirname "$0")/box-bootstrap.sh")"

cat <<WRAPPER
#!/bin/bash
# auto-generated startup-script from tools/deploy/provision-box.sh (ref: $PROVISION_REF)
set -euo pipefail
export PROVISION_REF='$(sq "$PROVISION_REF")'
export DEPLOY_PUBKEY='$(sq "$DEPLOY_PUBKEY")'
export OPERATOR_PUBKEY='$(sq "$OPERATOR_PUBKEY")'
export SEED_SA_KEY='$(sq "$SEED_SA_KEY")'
export SEED_PROJECT='$(sq "$SEED_PROJECT")'
export GCS_SEED='$(sq "$GCS_SEED")'
cat > /tmp/box-bootstrap.sh <<'BOOTSTRAP_EOF'
$BOOTSTRAP
BOOTSTRAP_EOF
chmod 700 /tmp/box-bootstrap.sh
bash /tmp/box-bootstrap.sh
rc=\$?
rm -f /tmp/box-bootstrap.sh   # shred the seed key after the build
exit \$rc
WRAPPER