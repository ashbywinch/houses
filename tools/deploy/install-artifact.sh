#!/bin/bash
# /opt/houses/install-artifact.sh <gs://bucket/<sha256>.tar.gz>
#
# THE rollout's only install step, and the sanctioned deploy-key shape. It:
#
#   1. fetches the artifact OBJECT with THIS INSTANCE's service-account identity
#      (metadata server — no key file, no secret in metadata);
#   2. verifies its sha256 EQUALS the object's key (content addressing: the
#      name is the proof, and a truncated or swapped upload cannot be served);
#   3. unpacks it over /opt/houses/app (code, frontend/dist, .venv, units,
#      tooling, migrations.list, checks) and records /opt/houses/ARTIFACT;
#   4. proves the venv actually imports the app — a broken venv must fail the
#      INSTALL, never the flip (2026-09-24: the flip aborted on a missing venv);
#   5. stops there. No database work: prepare restores the owner's live
#      snapshot onto this box, and the migrations and smoke run against THAT.
#      (There used to be a seed restore and a rehearsal here; it was a second,
#      stale data path — the smoke passed against data nobody was serving.)
#   6. leaves the app STOPPED. prepare starts it; only a human approves the flip.
#
# The app is stopped for the whole install; nothing here writes a database the
# runner is not operating on.
set -euo pipefail

ROOT="${HOUSES_ROOT:-/opt/houses}"
APP="$ROOT/app"

# A box that only needs to be CURRENT (never installed — e.g. the OWNER, whose
# only install is at bootstrap) can refresh its tooling without any app work.
# The deploy key is allowlisted for install-artifact.sh on every box, so the
# release keeps every box's /opt/houses scripts current before using a verb
# that a stale copy would not know (2026-09-30: the smoke relay died on the
# owner's pre-verb switch.sh).
TOOLING_ONLY=0
[ "${1:-}" = "--tooling-only" ] && { TOOLING_ONLY=1; shift; }
# This script runs from wherever the caller happened to be (the workflow SSH
# lands in the operator home): the venv python resolves top-level imports
# (``scripts``) from the CWD — ``cd`` to the app root or every smoke import
# chain that touches scripts.* crashes with ModuleNotFoundError (2026-09-30).
cd "$APP"
LOG_DIR="${HOUSES_LOG_DIR:-$ROOT/logs/releases}"
OBJECT="${1:?usage: install-artifact.sh gs://bucket/<sha256>.tar.gz}"
DB="$ROOT/data/houses.db"
ENV_FILE="${HOUSES_ENV_FILE:-/etc/houses.env}"
PORT=8765

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/install-artifact-$(date +%Y%m%d-%H%M%S).log"
# Every stage is mirrored to stdout (the workflow's log) and to the box log.
exec > >(tee -a "$LOG") 2>&1
mark() { echo "== $(date -u +%FT%TZ) $*"; logger -t houses-rollout "install-artifact $*" 2>/dev/null || true; }

# NEVER a silent death: any command that would trigger set -e logs the exact
# failing command line + rc to the box log AND the transcript before exiting.
# Without this, a pipe/grep or an unfenced curl inside the smoke died without a
# mark and the failure was undiscoverable (2026-09-30 forensics).
trap 'rc=$?; mark "FAILED at: $BASH_COMMAND (rc=$rc)"; exit $rc' ERR

[ "$(id -u)" = 0 ] || { echo "install-artifact: run as root" >&2; exit 1; }

# The object name IS the content hash. Refuse anything that is not exactly one
# 64-hex sha256 under a bucket: a charset check on the one value that reaches a
# subprocess argv (the deploy allowlist's injection discipline).
if ! printf '%s' "$OBJECT" | grep -Eq '^gs://[A-Za-z0-9._-]+/[0-9a-f]{64}\.tar\.gz$'; then
  mark "refusing: '$OBJECT' is not gs://<bucket>/<sha256>.tar.gz"
  exit 1
fi
EXPECTED_SHA=$(basename "$OBJECT" .tar.gz)

TARBALL=$(mktemp "$ROOT/.artifact-XXXXXX.tar.gz")
STAGE="$ROOT/.app-incoming"
cleanup() { rm -f "$TARBALL"; }
trap cleanup EXIT

# ── 1. the artifact ────────────────────────────────────────────────────
# A rollout rebuilds the box first, and the bootstrap installs exactly this
# object — re-fetching ~100 MB to unpack the same bytes is pure waste. The marker
# records the sha this checkout came from, so "already here" is a fact, not a
# guess. Anything else (a re-install, a different artifact, no marker) takes the
# full fetch/verify/unpack path.
# ── 1. the artifact ────────────────────────────────────────────────────
# A rollout rebuilds the box first, and the bootstrap installs exactly this
# object — re-fetching ~100 MB to unpack the same bytes is pure waste. The marker
# records the sha this checkout came from, so "already here" is a fact, not a
# guess. A TOOLING-only sync matches its own TOOLING marker (the tooling's
# provenance — the app's ARTIFACT marker is untouched by a tooling sync, so it
# keeps naming what the APP runs).
if [ "$TOOLING_ONLY" = 1 ]; then
  if grep -q "^sha256=$EXPECTED_SHA " "$ROOT/TOOLING" 2>/dev/null; then
    mark "tooling already at $EXPECTED_SHA — nothing to do (the app is untouched)"
    exit 0
  fi
elif grep -q "^sha256=$EXPECTED_SHA " "$ROOT/ARTIFACT" 2>/dev/null; then
  mark "the box already runs $EXPECTED_SHA (the bootstrap installed it) — skipping fetch and unpack"
  # The marker match IS the proof of equality; ACTUAL_SHA is what every later
  # message and the receipt are keyed on, so it must be set on this path too.
  ACTUAL_SHA="$EXPECTED_SHA"
  VENV_PY="$APP/.venv/bin/python"
fi

if [ -z "${ACTUAL_SHA:-}" ]; then
  mark "fetching $OBJECT with the instance identity"
  # 10 × 15 s: an IAM binding created with the box can still be propagating, and a
  # rollout that fails at the first fetch is a false alarm an operator has to
  # diagnose.
  fetched=0
  for attempt in $(seq 1 10); do
    if gsutil -q cp "$OBJECT" "$TARBALL"; then fetched=1; break; fi
    mark "fetch attempt $attempt/10 failed — retrying in 15s"
    sleep 15
  done
  [ "$fetched" = 1 ] || { mark "FAILED: could not fetch $OBJECT (no credentials? bucket IAM?)"; exit 1; }
  mark "fetched $(du -h "$TARBALL" | cut -f1)"

  # ── 2. the name IS the proof ─────────────────────────────────────────
  ACTUAL_SHA=$(sha256sum "$TARBALL" | cut -d' ' -f1)
  [ "$EXPECTED_SHA" = "$ACTUAL_SHA" ] || {
    mark "FAILED: sha256 mismatch — object key $EXPECTED_SHA, file $ACTUAL_SHA"
    exit 1
  }
  mark "sha256 verified: $ACTUAL_SHA"

  if [ "$TOOLING_ONLY" = 1 ]; then
    # TOOLING-ONLY: extract ONLY the tooling + units + ref into a staging tree.
    # The app, its venv, the database and the RUNNING app are never touched —
    # "no app work" is the point of the shape, and stopping the app here would
    # kill the flip's own preflight surface (2026-10-02: the sync stopped the
    # standby's app before the preflight that checks it). There is no venv
    # receipt to prove: nothing of the app is installed.
    mark "tooling-only: extracting tools/deploy + units + ARTIFACT_REF (the app is untouched)"
    rm -rf "$STAGE"
    mkdir -p "$STAGE"
    tar -xzf "$TARBALL" -C "$STAGE" tools/deploy units ARTIFACT_REF
  else
    # ── 3. unpack into a staging tree, PROVE it, then swap ───────────────
    # Never unpack over the live checkout: a file the new artifact dropped would
    # linger and shadow. The app is stopped first, and the swap only happens once
    # the staged venv has proven it can import the app — so a bad artifact leaves
    # the previous tree untouched.
    mark "stopping the app (nothing writes during an install)"
    systemctl stop houses.service 2>/dev/null || true
    rm -rf "$STAGE"
    mkdir -p "$STAGE"
    tar -xzf "$TARBALL" -C "$STAGE"
    mark "unpacked into $STAGE"
    VENV_PY="$STAGE/.venv/bin/python"
  fi
fi

# ── 4. the receipt: does this checkout import the app? (full installs only) ─
# A tooling-only sync installs nothing of the app and has nothing to prove here.
if [ "$TOOLING_ONLY" != 1 ]; then
[ -x "$VENV_PY" ] || { mark "FAILED: the artifact carries no runnable venv at $VENV_PY"; exit 1; }
# Run from / so the import can ONLY come from the venv: with the cwd inside the
# checkout, `import houses` would find the source tree and pass either way.
# (A venv built editable embeds the CI build paths and imports neither — that
# must fail HERE, not at the flip.)
if ! ( cd / && "$VENV_PY" -c 'import houses, dag' ) 2>&1; then
  mark "FAILED: the artifact's venv cannot import houses/dag — it is not relocatable"
  exit 1
fi
mark "venv receipt ok: $( "$VENV_PY" -c 'import sys; print(sys.version.split()[0])' ) imports houses + dag"
if [ "$VENV_PY" = "$STAGE/.venv/bin/python" ]; then
  RECEIPT_REF=$(head -1 "$STAGE/ARTIFACT_REF" 2>/dev/null || true)
  rm -rf "$ROOT/.app-previous"
  [ ! -d "$APP" ] || mv "$APP" "$ROOT/.app-previous"
  mv "$STAGE" "$APP"
  chown -R ubuntu:ubuntu "$APP"
  VENV_PY="$APP/.venv/bin/python"
  printf 'sha256=%s ref=%s unpacked_at=%s\n' \
    "$ACTUAL_SHA" "${RECEIPT_REF:-unknown}" "$(date -u +%FT%TZ)" > "$ROOT/ARTIFACT"
  chmod 644 "$ROOT/ARTIFACT"
  mark "installed: $(cat "$ROOT/ARTIFACT")"
fi
fi

# ── 5. layout + tooling from THIS artifact ─────────────────────────────
if [ "$TOOLING_ONLY" = 1 ]; then
  # The application tree is untouched and stays in place; box-setup installs
  # the tooling FROM THE STAGED TREE (HOUSES_ARTIFACT_TREE), so the box's
  # scripts become THIS artifact's, not the older app's.
  mark "refreshing box tooling from $ACTUAL_SHA (app + DB + live app untouched)"
  HOUSES_ARTIFACT_TREE="$STAGE" bash "$STAGE/tools/deploy/box-setup.sh"
  RECEIPT_REF=$(head -1 "$STAGE/ARTIFACT_REF" 2>/dev/null || true)
  printf 'sha256=%s ref=%s installed_at=%s\n' \
    "$ACTUAL_SHA" "${RECEIPT_REF:-unknown}" "$(date -u +%FT%TZ)" > "$ROOT/TOOLING"
  chmod 644 "$ROOT/TOOLING"
  # Re-exec from the REFRESHED copy when it differs: this script is running
  # from the PREVIOUS artifact's snapshot, and the sync has just overwritten
  # it with the NEW tooling. Without the re-exec every tooling fix lags exactly
  # one run behind. The second pass sees the TOOLING marker and stops. The
  # staging tree is removed before the re-exec either way — a sync leaves no
  # app work behind.
  if [ -f /opt/houses/install-artifact.sh ] && ! cmp -s "$0" /opt/houses/install-artifact.sh; then
    rm -rf "$STAGE"
    mark "re-executing the refreshed install-artifact.sh (content changed)"
    exec /opt/houses/install-artifact.sh --tooling-only "$OBJECT"
  fi
  rm -rf "$STAGE"
  mark "TOOLING now at $ACTUAL_SHA — the app was not stopped, not swapped, not started"
  exit 0
fi
mark "refreshing box layout and tooling from the artifact"
bash "$APP/tools/deploy/box-setup.sh"
# Re-exec from the REFRESHED copy when it differs: this script is running
# from the PREVIOUS artifact's snapshot, and the refresh has just overwritten
# it with the NEW tooling. Without the re-exec every deploy-script fix
# lags exactly one run behind (2026-09-30 — the cd fix and the mint move
# each executed once too late). The second pass sees identical content and
# stops. $OBJECT is the same content-addressed reference; the fetch/unpack
# skip on sha match, so the pass is cheap.
if [ -f /opt/houses/install-artifact.sh ] && ! cmp -s "$0" /opt/houses/install-artifact.sh; then
  mark "re-executing the refreshed install-artifact.sh (content changed)"
  exec /opt/houses/install-artifact.sh "$OBJECT"
fi

# ── 6. install receipt ─────────────────────────────────────────────────
# No data work and no smoke here. This box holds no database of its own until
# `prepare` restores the owner's LIVE snapshot onto it — and that is where the
# migrations run and the smoke runs, on the data that will actually serve.
# The old seed restore and the seed rehearsal were a second, stale data path.
mark "artifact installed: $ACTUAL_SHA on $(hostname) — no data yet; prepare restores the owner's snapshot"
