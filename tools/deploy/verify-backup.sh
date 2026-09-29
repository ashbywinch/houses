#!/usr/bin/env bash
# tools/deploy/verify-backup.sh — is this database a GOOD recovery source?
#
# usage:  verify-backup.sh <gs://bucket/object.db | /path/to/file.db>
#
# These are the SAME gates the rollout itself insists on, run on a copy:
#   1. PRAGMA integrity_check == ok,
#   2. node_results has rows,
#   3. EVERY migration applies AND its paired check passes — the runner's verdict
#      `migrations: <N> applied+checked, 0 failed`.
#
# THE LIMIT OF THIS CHECK — read this before trusting the output. Passing gates
# proves the TOOLING can carry the database forward (a mechanically usable
# recovery source). It does NOT prove the DATA is what you want: the migration
# machinery on a copy can repair a database whose source was broken, and this
# project has already paid for that confusion once (the 2026-09-24 migration that
# never ran). A backup is TRUSTED only when you know when and from which box it
# was captured, and a human judges that moment trustworthy. The recovery action's
# approval gate is where that judgement happens — this script only feeds it.
#
# The source is always copied first (gsutil for an object, cp for a file), so
# verification never mutates the backup.
#
# Where backups live: gs://houses-seed/ (the human-validated seed + its .meta),
# and each box's own /opt/houses/data/houses.db while the box exists (see
# /opt/houses/RESTORED for what the DB was last restored from).
set -euo pipefail

SRC="${1:?usage: verify-backup.sh <gs://.../object.db | /path/file.db>}"
APP="${APP:-$(cd "$(dirname "$0")/../.." && pwd)}"
PY="$APP/.venv/bin/python"
[ -x "$PY" ] || { echo "verify-backup: no venv at $PY (run from a checkout with dependencies)" >&2; exit 2; }

WORK="$(mktemp -d /var/tmp/houses-verify.XXXXXX)"  # NOT /tmp: the copy + migration
# backup + WAL need room (the live copy is ~2.3 GB)
trap 'rm -rf "$WORK"' EXIT

echo "== verifying $SRC (copied first — the source is never touched)"
DB="$WORK/copy.db"
case "$SRC" in
  gs://*) gsutil -q cp "$SRC" "$DB" ;;
  *) cp "$SRC" "$DB" ;;
esac

INTEGRITY=$(sqlite3 "$DB" "PRAGMA integrity_check;" | head -1)
ROWS=$(sqlite3 "$DB" "SELECT count(*) FROM node_results;" 2>/dev/null || echo 0)
echo "integrity_check: $INTEGRITY"
echo "node_results rows: $ROWS"
if [ "$INTEGRITY" != "ok" ] || [ "$ROWS" = "0" ]; then
  echo "NOT USABLE — the copy fails integrity or has no data"
  exit 1
fi

echo "== migrations (the rollout's own runner + manifest, on the copy)"
HOUSES_ROOT="$WORK" HOUSES_LOG_DIR="$WORK/logs" "$PY" "$APP/tools/deploy/run_migrations.py" \
  --manifest "$APP/tools/deploy/migrations.list" \
  --db "$DB" \
  --scripts-dir "$APP" \
  --python "$PY" \
  --apply > "$WORK/run.log" 2>&1 || { echo "BAD — the runner failed:" >&2; tail -20 "$WORK/run.log" >&2; exit 1; }
if ! grep -Eq '^migrations: [0-9]+ applied\+checked, 0 failed$' "$WORK/run.log"; then
  echo "BAD — the migration gate did not pass:" >&2
  tail -20 "$WORK/run.log" >&2
  exit 1
fi
grep -E '^migrations:' "$WORK/run.log"

echo "MIGRATION-COMPATIBLE: integrity ok, $ROWS rows, every migration applied AND its paired check passed on a copy"
echo "   -> the tooling can carry this object forward; safe to NAME it in action=recover."
echo "NOT a data-trust verdict: decide that from when/from which box it was captured"
echo "   (see the object's .meta and tools/deploy/provision.md §8). Trust stays a HUMAN judgement."