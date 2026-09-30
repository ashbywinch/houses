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
#   5. runs the migration runner (rehearsal: apply + check every migration)
#      against THIS box's database copy, then the smoke on 127.0.0.1;
#   6. leaves the app STOPPED. The cutover starts it (and only after a human has
#      read this transcript and approved).
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
if grep -q "^sha256=$EXPECTED_SHA " "$ROOT/ARTIFACT" 2>/dev/null; then
  mark "the box already runs $EXPECTED_SHA (the bootstrap installed it) — skipping fetch and unpack"
  # The marker match IS the proof of equality; ACTUAL_SHA is what every later
  # message and the receipt are keyed on, so it must be set on this path too.
  ACTUAL_SHA="$EXPECTED_SHA"
  VENV_PY="$APP/.venv/bin/python"
else
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

# ── 4. the receipt: does this checkout import the app? ─────────────────
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

# ── 5. layout + tooling from THIS artifact ─────────────────────────────
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
  exec /opt/houses/install-artifact.sh --tooling-only "$OBJECT"
fi
[ "$TOOLING_ONLY" = 1 ] && { mark "tooling refreshed only — no app work (owner role)"; exit 0; }

# ── 6. migration rehearsal: apply + check every migration ──────────────
# The runner's summary line is the gate: exit 0 is not enough, and the runner
# itself refuses a zero-migration manifest (the 2026-09-24 silent skip).
#
# A box with no database was never seeded: the rehearsal cannot run and this is
# NOT a rollout-ready box (its data would be nothing). Fail loudly rather than
# leave an operator guessing.
[ -s "$DB" ] || {
  mark "FAILED: no database at $DB — this box was never seeded (tools/deploy/seed-box.sh) or the bootstrap's seed restore failed"
  exit 1
}
mark "migration rehearsal on $DB (one run_migrations.py call)"
MIG_RC=0
MIG_OUT=$("$VENV_PY" "$APP/tools/deploy/run_migrations.py" \
  --manifest "$APP/tools/deploy/migrations.list" \
  --db "$DB" \
  --scripts-dir "$APP" \
  --python "$VENV_PY" \
  --apply 2>&1) || MIG_RC=$?
printf '%s\n' "$MIG_OUT"
if [ "$MIG_RC" != 0 ] || ! printf '%s\n' "$MIG_OUT" | grep -Eq '^migrations: [0-9]+ applied\+checked, 0 failed$'; then
  mark "FAILED: the migration rehearsal did not report applied+checked — the app stays stopped"
  exit 1
fi
mark "migration rehearsal verified"

# ── 7. smoke on 127.0.0.1 ──────────────────────────────────────────────
# R4 — memory gate: never start the app on a starved box (2026-09-07: a second
# stack on a 953 MiB e2-micro OOM-killed the guest). Nothing else runs here —
# the app was stopped for the unpack — so there is no leaked growth to reap and
# no remedy: refuse.
FREE_MB=$(awk '/MemAvailable/ { print int($2 / 1024) }' /proc/meminfo)
if [ "$FREE_MB" -lt 450 ]; then
  mark "FAILED: only ${FREE_MB} MiB free — refusing to start the app on a starved box"
  exit 1
fi
mark "memory pre-flight ok (${FREE_MB} MiB free)"

# The standby's role URL is the smoke hostname (the review surface). The
# default from the seed env is production; the flip re-sets the new owner.
# ONE writer of the role URL: switch.sh --public-url owns the env-file logic
# (same sed/append, one place) — install just declares the STANDBY's role.
bash "$ROOT/switch.sh" --public-url https://houses-smoke.blueumbrella.net
mark "standby public URL: houses-smoke.blueumbrella.net"

# The authenticated smoke needs a superuser cookie minted with the app's own
# code. The mint runs while the app is STOPPED, on idle memory: minting during
# the smoke would import the full app tree WHILE the fresh process ground
# through its property-DAG startup load and fail in that pressure window —
# idle it succeeds (verified as root and non-root; the memory gate above has
# just confirmed headroom). The cookie does not need the app running.
SECRET=$(grep '^HOUSES_SESSION_SECRET=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)
[ -n "$SECRET" ] || { mark "FAILED: HOUSES_SESSION_SECRET missing from $ENV_FILE"; exit 1; }
# The cookie is CAPTURED, never echoed: the print goes into the substitution
# and the curls use it only in a -H header (-v is never used), so the
# superuser cookie cannot appear in this transcript. The mint's STDERR is
# captured to a file and surfaced on failure — a failing mint names its
# traceback, not a generic message.
MINT_ERR=$(mktemp)
COOKIE=$(HOUSES_SESSION_SECRET="$SECRET" "$VENV_PY" -c '
from houses.web.auth import _make_session_cookie
print(_make_session_cookie(email="simon@example.com", name="Simon", picture="", is_superuser=True))
' 2>"$MINT_ERR" </dev/null) || {
  mark "FAILED: the smoke cookie could not be minted — traceback:"
  cat "$MINT_ERR" >&2
  rm -f "$MINT_ERR"
  exit 1
}
rm -f "$MINT_ERR"
mark "smoke cookie minted (not echoed)"

mark "starting the app for the smoke"
systemctl restart houses.service
# A fresh box's FIRST start is cold: restored DB (100+ MB) + the property-DAG
# load, measured ~5.5 min on an e2-micro (2026-09-30). 10 min window, still
# loud: a box that is not healthy by then is genuinely stuck.
for i in $(seq 1 300); do
  curl -fsS --max-time 5 "localhost:$PORT/health" >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS --max-time 30 "localhost:$PORT/health" >/dev/null 2>&1 || {
  mark "FAILED: the app did not become healthy on :$PORT"
  journalctl -u houses.service --since="5 minutes ago" --no-pager 2>/dev/null | tail -40 || true
  exit 1
}
mark "app healthy on :$PORT"

mark "smoke: /health"
curl -fsS --max-time 180 "localhost:$PORT/health" | grep -qE '"status": ?"ok"' || {
  mark "FAILED: smoke /health did not report status ok"
  exit 1
}

mark "smoke: /api/properties/all (>= 1 house record)"
ALL=$(curl -fsS --max-time 1800 -H "Cookie: session=$COOKIE" "localhost:$PORT/api/properties/all") || {
  mark "FAILED: smoke /api/properties/all did not answer"
  exit 1
}
RIDS=$(printf '%s' "$ALL" | "$VENV_PY" -c 'import json,sys; d=json.load(sys.stdin); print(len(d.get("properties", d)))' 2>/dev/null || echo 0)
echo "   house records served: $RIDS"
[ "$RIDS" -gt 0 ] || { mark "FAILED: smoke /api/properties/all returned no house records"; exit 1; }

echo "== smoke: a property detail with commutes"
RID=$(printf '%s' "$ALL" | "$VENV_PY" -c 'import json,sys; d=json.load(sys.stdin); ps=d.get("properties", d); print(sorted(ps)[-1] if isinstance(ps, dict) else ps[0]["rid"])' 2>/dev/null || echo "")
if [ -n "$RID" ]; then
  curl -fsS --max-time 900 -H "Cookie: session=$COOKIE" "localhost:$PORT/api/properties/$RID/detail" >/dev/null
fi

echo "== smoke: frontend index (HTTP 200 + HTML)"
INDEX_FILE=$(mktemp /tmp/houses-smoke-index-XXXXXX.html)
F_HTTP=$(curl -sS --max-time 10 -o "$INDEX_FILE" -w "%{http_code}" "localhost:$PORT/")
echo "   frontend HTTP $F_HTTP"
if [ "$F_HTTP" != "200" ] || ! grep -qi "<!doctype html\|<html" "$INDEX_FILE"; then
  mark "FAILED: the shipped frontend did not serve (HTTP $F_HTTP)"
  rm -f "$INDEX_FILE"
  exit 1
fi
echo "   frontend html ok ($(wc -c < "$INDEX_FILE") bytes)"
rm -f "$INDEX_FILE"

echo "== smoke: scrape-queue health (worker liveness)"
STATUS=$(curl -fsS --max-time 10 -H "Cookie: session=$COOKIE" "localhost:$PORT/api/scrapes/status" || echo '{"scrapes":{}}')
echo "   queue: $STATUS"
PENDING=$(printf '%s' "$STATUS" | "$VENV_PY" -c 'import json,sys; print(json.load(sys.stdin).get("scrapes", {}).get("pending", 0))' 2>/dev/null || echo 0)
if [ "$PENDING" -gt 0 ]; then
  echo "WARNING: $PENDING scrape job(s) pending — the LAN scrape worker may be down (journalctl -u houses-scrape-worker on the LAN machine)."
fi

# ── 8. the standby waits, stopped ──────────────────────────────────────
# The standby IS the review surface: it stays RUNNING at houses-smoke until the
# human approves the flip (the process wrote its smoke URL before the smoke).
mark "REVIEW READY: artifact $ACTUAL_SHA on $(hostname) — smoke verified at http://localhost:$PORT"
mark "app is SERVING at houses-smoke.blueumbrella.net — the human reviews it, then approves the flip"
