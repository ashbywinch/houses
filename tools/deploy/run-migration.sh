#!/bin/bash
# /opt/houses/run-migration.sh — run a ref-shipped data migration against a
# database, under the release's safety contract. THE generic step: any
# future migration (create the script, add it to release.sh's MIGRATIONS
# list) gets the same space gate, dry-run, apply, verify, backup and
# transcript — no new deployment machinery per migration.
#
# Usage:
#   run-migration.sh <script> <db> <python>            # dry-run + apply + verify
#   run-migration.sh --dry-run <script> <db> <python>  # read-only report only
#
# Called by release.sh on the STANDBY's smoke copy (a verified snapshot of
# the live DB) before the standby boots: the live DB is never touched, and a
# failure aborts the release with prod untouched. Scripts must be idempotent
# so a re-release after adoption finds nothing to do.
#
# Migration script contract (the reference implementation is
# scripts/backfill_person_ids.py):
#   - dry-run by default with NO arguments (read-only, prints what it would
#     do);   --apply   writes (honours HOUSES_SCRIPTS_MAY_WRITE=1);
#     --backup  writes a `<db>.pre-<migration>` copy first;   --verify
#     re-scans after apply and must find nothing left to do.
#   - batched commits: a single-transaction run needed a ~2.4 GB rollback
#     journal and crashed the box out-of-disk (2026-09-18).
#
# Space gate: refuse below DB size + headroom free. Tests set
# HOUSES_MIGRATION_MIN_FREE_BYTES to pin the refusal without touching df.
set -euo pipefail

DRY_ONLY=0
if [ "${1:-}" = "--dry-run" ]; then
  DRY_ONLY=1
  shift
fi

SCRIPT="${1:?usage: run-migration.sh [--dry-run] <script> <db> <python>}"
DB="${2:?usage: run-migration.sh [--dry-run] <script> <db> <python>}"
PY="${3:?usage: run-migration.sh [--dry-run] <script> <db> <python>}"

ROOT="${HOUSES_ROOT:-/opt/houses}"
LOG_DIR="${HOUSES_LOG_DIR:-$ROOT/logs/releases}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/run-migration-$(date +%Y%m%d-%H%M%S)-$(basename "$SCRIPT").log"
exec > >(tee -a "$LOG") 2>&1
mark() { echo "== $(date +%H:%M:%S) $*"; logger -t houses-release "run-migration $*" 2>/dev/null || true; }

# The ref must actually ship the script it lists — silence here is drift
# (a release that forgot its migration would quietly skip state changes).
[ -f "$SCRIPT" ] || { mark "ref is missing its migration script: $SCRIPT"; exit 1; }
[ -f "$DB" ] || { mark "database not found: $DB"; exit 1; }
[ -x "$PY" ] || { mark "python not found: $PY"; exit 1; }

DB_SIZE=$(du -b "$DB" | cut -f1)
DEFAULT_NEED=$((DB_SIZE + 1073741824))  # DB + 1 GiB journal/backup headroom
NEED="${HOUSES_MIGRATION_MIN_FREE_BYTES:-$DEFAULT_NEED}"
FREE_DISK=$(df -B1 --output=avail "$(dirname "$DB")" | tail -1)
if [ "$FREE_DISK" -lt "$NEED" ]; then
  mark "refusing: need ${NEED} bytes free beside the DB, have ${FREE_DISK} (2026-09-18 out-of-disk crash)"
  exit 1
fi
mark "space gate ok (${FREE_DISK} bytes free, need ${NEED})"

# 1. Dry-run (read-only): the migration prints what the apply will do.
mark "dry-run on $DB"
"$PY" "$SCRIPT" --db "$DB"
if [ "$DRY_ONLY" = 1 ]; then
  mark "dry-run only — no writes"
  exit 0
fi

# 2. Apply with the script's own pre-migration backup and post-apply
#    verify. Batched commits bound the rollback journal; the run is
#    resumable after any interruption.
mark "apply + verify (with pre-migration backup)"
if ! HOUSES_SCRIPTS_MAY_WRITE=1 "$PY" "$SCRIPT" --db "$DB" --apply --backup --verify; then
  mark "migration FAILED on $DB — the live DB is untouched; the pre-migration copy is $DB.pre-* backup"
  exit 1
fi

# 3. Ownership: release.sh runs as root, the app unit runs as ubuntu — the
#    smoke DB must be readable by the standby. The migration's own backup
#    stays root-owned on purpose: it is a forensic revert layer, never read
#    by the app.
if id ubuntu >/dev/null 2>&1; then
  chown ubuntu:ubuntu "$DB" 2>/dev/null || true
fi
chmod 600 "$DB" 2>/dev/null || true
mark "migration applied + verified"