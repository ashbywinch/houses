#!/bin/bash
# /opt/houses bootstrap — rendered by terraform into the instance's
# `startup-script` metadata (terraform/startup.sh.tftpl) and executed ONCE at
# first boot of a fresh instance. The WHOLE box build:
#
#   packages → operator SSH key → artifact (code + venv + frontend + tooling)
#   → box layout/units/sudoers → deploy-key allowlist → data seed (instance SA)
#   → migrations on the seed → app env → Caddy HTTPS → markers.
#
# Inputs arrive as PUBLIC environment variables (a git ref, an artifact object,
# two SSH public keys, bucket names) — no secret, no key file: every gsutil call
# here uses THIS INSTANCE's service-account identity (metadata server). See
# docs/anti-fragile-rollout-plan.md.
#
# ORDER MATTERS (2026-09-23 postmortem): the operator's admin key is installed
# BEFORE anything heavy, so even a half-provisioned box is reachable and
# troubleshootable; the deploy key's allowlist needs the artifact's own copy of
# install-deploy-allowlist.sh, so it lands immediately after the unpack — still
# before the seed, the migrations and Caddy (the steps that can fail).
#
# Idempotent-safe: re-running it re-fetches the artifact and re-runs the
# migrations (which are idempotent). Replace-not-repair: never hand-fix a box.
set -euo pipefail

log() { echo "== $*"; }

# gsutil with the instance's service-account identity (metadata server — no key
# file). Retried, because a binding created in the same Terraform apply can still
# be propagating while the first boot runs.
FETCH_ATTEMPTS=10
FETCH_DELAY_SECONDS=15

fetch_object() {
  attempt=1
  while [ "$attempt" -le "$FETCH_ATTEMPTS" ]; do
    gsutil -q cp "$1" "$2" && return 0
    log "fetch of $1 failed (attempt $attempt/$FETCH_ATTEMPTS) — retrying in ${FETCH_DELAY_SECONDS}s"
    sleep "$FETCH_DELAY_SECONDS"
    attempt=$((attempt + 1))
  done
  return 1
}
log "0. inputs"
: "${PROVISION_REF:?} ${PROVISION_ARTIFACT:?} ${OPERATOR_PUBKEY:?}"
DEPLOY_PUBKEY="${DEPLOY_PUBKEY:-}"
GCS_SEED="${GCS_SEED:-gs://houses-seed/latest.db}"
GCS_ENV="${GCS_ENV:-gs://houses-seed/houses.env}"
HOUSES_MAIN_HOST="${HOUSES_MAIN_HOST:-houses.blueumbrella.net}"
export HOUSES_ROOT=/opt/houses
ROOT=/opt/houses
APP="$ROOT/app"

log "1. packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq sqlite3 curl ca-certificates gnupg debian-archive-keyring openssh-client >/dev/null

# gsutil is the box's ONLY route to the bucket (artifact, seed, env, cert cache)
# and the GCP Ubuntu image is expected to carry the CLI — the previous bootstrap
# authenticated a service-account key with it. Install it if an image ever ships
# without one, and fail loudly rather than half-building a box.
command -v gsutil >/dev/null 2>&1 || apt-get install -y -qq google-cloud-cli >/dev/null
command -v gsutil >/dev/null 2>&1 || { log "FAILED: no gsutil — cannot fetch the artifact/seed"; exit 1; }

log "2. operator admin key FIRST — the box is ssh-able even if the rest fails"
OPERATOR_HOME=$(getent passwd ubuntu | cut -d: -f6)
mkdir -p "$OPERATOR_HOME/.ssh"
grep -qF "$OPERATOR_PUBKEY" "$OPERATOR_HOME/.ssh/authorized_keys" 2>/dev/null || \
  printf '%s\n' "$OPERATOR_PUBKEY" >> "$OPERATOR_HOME/.ssh/authorized_keys"
chown -R ubuntu:ubuntu "$OPERATOR_HOME/.ssh"
chmod 700 "$OPERATOR_HOME/.ssh" && chmod 600 "$OPERATOR_HOME/.ssh/authorized_keys"

log "3. the artifact: fetch, verify (the object KEY is the sha256), unpack"
# install-artifact.sh owns the rollout version of this (fetch + receipt +
# rehearsal + smoke). A fresh box cannot run it — it ships INSIDE the artifact —
# so the three proof steps are repeated here: name charset, sha256 == key,
# unpack. Keep the two in step; both are exercised on every rollout.
case "$PROVISION_ARTIFACT" in
  gs://*/*.tar.gz) : ;;
  *) log "FAILED: PROVISION_ARTIFACT '$PROVISION_ARTIFACT' is not gs://<bucket>/<sha256>.tar.gz"; exit 1 ;;
esac
printf '%s' "$PROVISION_ARTIFACT" | grep -Eq '^gs://[A-Za-z0-9._-]+/[0-9a-f]{64}\.tar\.gz$' || {
  log "FAILED: PROVISION_ARTIFACT must be gs://<bucket>/<sha256>.tar.gz"; exit 1; }
EXPECTED_SHA=$(basename "$PROVISION_ARTIFACT" .tar.gz)
install -d -o ubuntu -g ubuntu "$ROOT" "$ROOT/data" "$ROOT/logs/releases"
TARBALL=$(mktemp "$ROOT/.bootstrap-artifact-XXXXXX.tar.gz")
# 10 × 15 s: a fresh instance's service-account IAM may still be propagating when
# the first boot fetches (a new binding can take a minute to be honoured), so the
# loop is patient and bounded rather than a single attempt.
fetch_object "$PROVISION_ARTIFACT" "$TARBALL" || { log "FAILED: cannot fetch $PROVISION_ARTIFACT with the instance identity"; exit 1; }
ACTUAL_SHA=$(sha256sum "$TARBALL" | cut -d' ' -f1)
[ "$EXPECTED_SHA" = "$ACTUAL_SHA" ] || { log "FAILED: sha256 mismatch ($EXPECTED_SHA != $ACTUAL_SHA)"; exit 1; }
mkdir -p "$APP"
tar -xzf "$TARBALL" -C "$APP"
rm -f "$TARBALL"
chown -R ubuntu:ubuntu "$APP"
printf 'sha256=%s ref=%s unpacked_at=%s\n' "$ACTUAL_SHA" "$PROVISION_REF" "$(date -u +%FT%TZ)" > "$ROOT/ARTIFACT"
chmod 644 "$ROOT/ARTIFACT"
log "artifact verified + unpacked: $(cat "$ROOT/ARTIFACT")"

VENV_PY="$APP/.venv/bin/python"
[ -x "$VENV_PY" ] || { log "FAILED: the artifact carries no runnable venv at $VENV_PY"; exit 1; }
# From / so the import can ONLY come from the venv (with the cwd inside the
# checkout, `import houses` would find the source tree and pass either way).
( cd / && "$VENV_PY" -c 'import houses, dag' ) || { log "FAILED: the artifact venv cannot import houses/dag"; exit 1; }
log "venv receipt ok"

log "4. layout + units + sudoers (idempotent)"
bash "$APP/tools/deploy/box-setup.sh"

log "5. the deploy key's forced-command allowlist"
if [ -n "$DEPLOY_PUBKEY" ]; then
  "$ROOT/install-deploy-allowlist.sh" "$DEPLOY_PUBKEY"
fi

log "6. data seed from GCS (the human-validated seed; instance identity)"
if [ ! -s "$ROOT/data/houses.db" ]; then
  # FATAL, not a warning: a box with no data cannot serve, and the PROVISIONED
  # marker below is what the provision job waits for — declaring an empty box
  # provisioned is how a silent failure becomes a rollout.
  fetch_object "$GCS_SEED" "$ROOT/data/houses.db" \
    || { log "FAILED: no seed at $GCS_SEED — refusing to declare this box provisioned"; exit 1; }
fi
if [ -s "$ROOT/data/houses.db" ]; then
  chown ubuntu:ubuntu "$ROOT/data/houses.db" && chmod 600 "$ROOT/data/houses.db"
  sqlite3 "$ROOT/data/houses.db" "PRAGMA integrity_check;" | grep -q '^ok$' || { log "FAILED: seed integrity check"; exit 1; }
  # Provenance: "what did this box start from" must be a file, not a memory.
  # rows = the seed's row count at restore; etag = the object's identity in the
  # bucket at that moment. Printed by switch.sh --diagnose. The seed is NEVER
  # auto-refreshed (that mechanism — --publish — is deleted): a fresh box's data
  # is seed + migrations, by design, and seed-box.sh is the deliberate,
  # human-validated refresh.
  SEED_ETAG=$(gsutil ls -L "$GCS_SEED" 2>/dev/null | awk -F': +' '/^[[:space:]]*ETag:/ {print $2; exit}' || true)
  SEED_ROWS=$(sqlite3 "$ROOT/data/houses.db" "SELECT count(*) FROM node_results;" 2>/dev/null || echo unknown)
  printf 'object=%s etag=%s restored_at=%s rows=%s\n' \
    "$GCS_SEED" "${SEED_ETAG:-unknown}" "$(date -u +%FT%TZ)" "$SEED_ROWS" > "$ROOT/SEED"
  chmod 644 "$ROOT/SEED"
  log "seeded from $GCS_SEED ($(cat "$ROOT/SEED"))"

  log "6b. migrations on the restored seed (seed + migrations = this box's data)"
  MIG_RC=0
  MIG_OUT=$("$VENV_PY" "$APP/tools/deploy/run_migrations.py" \
    --manifest "$APP/tools/deploy/migrations.list" \
    --db "$ROOT/data/houses.db" \
    --scripts-dir "$APP" \
    --python "$VENV_PY" \
    --apply 2>&1) || MIG_RC=$?
  printf '%s\n' "$MIG_OUT"
  if [ "$MIG_RC" != 0 ] || ! printf '%s\n' "$MIG_OUT" | grep -Eq '^migrations: [0-9]+ applied\+checked, 0 failed$'; then
    log "FAILED: the seed could not be carried forward by the migrations"
    exit 1
  fi
else
  log "no seed at $GCS_SEED — box starts empty (the rollout's install still verifies the artifact)"
fi

log "7. app env restore (private GCS object — instance identity)"
if [ ! -f /etc/houses.env ]; then
  gsutil -q cp "$GCS_ENV" /tmp/houses.env 2>/dev/null || true
  if [ -s /tmp/houses.env ]; then
    install -m 600 -o root -g root /tmp/houses.env /etc/houses.env
    rm -f /tmp/houses.env
    log "env restored to /etc/houses.env"
  else
    # Same reasoning as the seed: without it the app cannot start, so the box is
    # not provisioned and must not say it is.
    log "FAILED: no env object at $GCS_ENV — refusing to declare this box provisioned"
    exit 1
  fi
fi
# The hostname is declarative here (terraform/variables.tf → metadata) instead of
# a hand-edited line in the secrets file.
if [ -f /etc/houses.env ] && ! grep -q '^HOUSES_MAIN_HOST=' /etc/houses.env; then
  printf 'HOUSES_MAIN_HOST=%s\n' "$HOUSES_MAIN_HOST" >> /etc/houses.env
fi

log "8. HTTPS — Caddy on :443, reverse-proxying the app's single port"
if [ -f /etc/houses.env ]; then
  bash "$APP/tools/deploy/install-caddy.sh"
else
  log "no /etc/houses.env yet — caddy install deferred to the env step"
fi

log "9. provision marker"
echo "provisioned $(date -u +%FT%TZ) ref=$PROVISION_REF artifact=$ACTUAL_SHA" > "$ROOT/PROVISIONED"
chmod 644 "$ROOT/PROVISIONED"

log "PROVISION COMPLETE — ssh paths live, artifact installed, seed+migrations applied, box ready for the rollout"
