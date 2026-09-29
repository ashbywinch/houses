#!/usr/bin/env bash
# tools/deploy/seed-box.sh — produce the data seed for a fresh box.
#
# Run on the LAN dev machine, against the box that CURRENTLY OWNS THE TRAFFIC
# (the L4 rule's target — `gcloud compute forwarding-rules describe houses-l4-https
# --region us-west1 --format='value(target)'`), reached with the operator key
# (~/.ssh/houses_operator) and gcloud's own login for the bucket.
#
# This is the migration REHEARSAL: the live DB is copied, the SAME manifest the
# rollout uses is applied to the COPY by the SAME runner, and the result becomes
# gs://houses-seed/latest.db — what box-bootstrap.sh restores on a fresh instance.
# There is no second list of migrations here: a hand-written pair of script names
# beside `migrations.list` would be a second mechanism, and the first migration
# added to the manifest would silently not be in the seed.
#
# The seed is NEVER auto-refreshed: a fresh box's data is seed + migrations, by
# design, and THIS script is the deliberate, human-validated refresh. Run it when
# a human judges the settled state trustworthy. The bucket is VERSIONED
# (last 10 generations kept), so this upload never destroys the previous seed.
#
# usage:  tools/deploy/seed-box.sh <owner-ip>       # the rule's target instance
set -euo pipefail

BOX_IP="${1:?usage: seed-box.sh <owner-ip>   (the L4 rule target instance)}"
OPERATOR_KEY="${OPERATOR_KEY:-$HOME/.ssh/houses_operator}"
WORK="$(mktemp -d /var/tmp/houses-seed.XXXXXX)"  # NOT /tmp: a small tmpfs
# overflows while the migration journals (2.3 GB DB + backup + update WAL)
SEED_GS="${SEED_GS:-gs://houses-seed/latest.db}"
APP="${APP:-$(cd "$(dirname "$0")/../.." && pwd)}"
PY="$APP/.venv/bin/python"
trap 'rm -rf "$WORK"' EXIT

echo "== 1/4 pull the live DB (read-only)"
scp -i "$OPERATOR_KEY" -o StrictHostKeyChecking=accept-new \
  "ubuntu@$BOX_IP:/opt/houses/data/houses.db" "$WORK/live.db"
ls -la "$WORK/live.db"

echo "== 2/4 migrate the COPY with the rollout's own runner + manifest"
# Writes its pre-migration backup beside the copy (2.3 GB: /var/tmp, never /tmp),
# runs every migration's apply AND its paired check, and prints the verdict.
set +e
"$PY" "$APP/tools/deploy/run_migrations.py" \
  --manifest "$APP/tools/deploy/migrations.list" \
  --db "$WORK/live.db" \
  --scripts-dir "$APP" \
  --python "$PY" \
  --apply | tee "$WORK/migrations.log"
MIG_RC=${PIPESTATUS[0]}
set -e
if [ "$MIG_RC" != 0 ] || ! grep -Eq '^migrations: [0-9]+ applied\+checked, 0 failed$' "$WORK/migrations.log"; then
  echo "FAILED: the seed copy did not migrate cleanly — nothing was uploaded" >&2
  exit 1
fi

echo "== 3/4 integrity"
sqlite3 "$WORK/live.db" "PRAGMA integrity_check;" | grep -q '^ok$' \
  || { echo "FAILED: the migrated copy fails integrity_check — nothing was uploaded" >&2; exit 1; }

echo "== 4/4 upload the seed, and the meta a future agent can read WITHOUT the copy"
ROWS=$(sqlite3 "$WORK/live.db" "SELECT count(*) FROM node_results;")
APPLIED=$(sed -n 's/^migrations: \([0-9]*\) applied+checked.*/\1/p' "$WORK/migrations.log" | head -1)
{
  printf 'object=%s\n' "$SEED_GS"
  printf 'captured_from=%s\n' "$BOX_IP"
  printf 'captured_at=%s\n' "$(date -u +%FT%TZ)"
  printf 'rows=%s\n' "$ROWS"
  printf 'manifest_sha256=%s\n' "$(sha256sum "$APP/tools/deploy/migrations.list" | cut -d' ' -f1)"
  printf 'migrations_applied=%s\n' "${APPLIED:-1}"
  printf 'gates=passed-on-copy (integrity + every paired check) — mechanical usability only\n'
  printf 'made_by=%s\n' "${USER:-unknown}"
  printf 'trust=unset (a HUMAN decides: is the capture moment trustworthy? see provision.md §8)\n'
} > "$WORK/latest.db.meta"
gsutil cp "$WORK/live.db" "$SEED_GS"
gsutil cp "$WORK/latest.db.meta" "$SEED_GS.meta"

echo "seed ready: $SEED_GS $(stat -c%s "$WORK/live.db") bytes, rows=$ROWS"
echo "provenance: $SEED_GS.meta — and a no-context agent verifies it with:"
echo "  tools/deploy/verify-backup.sh $SEED_GS"
echo "NEXT: gh workflow run Release -f action=release     # build + install on the standby"
echo "then: gh workflow run Release -f action=cutover     # the gated flip"
