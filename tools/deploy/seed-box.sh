#!/bin/bash
# tools/deploy/seed-box.sh — produce the data seed for a fresh box.
#
# Run on the LAN dev machine (the box is reached with the operator key
# from ~/.ssh/oracle; the seed bucket via the local gcloud login). This
# is the migration REHEARSAL the rollout always wanted: the live DB is
# copied, the person-id migration is applied to the COPY (never live),
# integrity is verified, and the result becomes gs://houses-seed/latest.db
# — what box-bootstrap.sh restores on a fresh instance.
#
# usage:  tools/deploy/seed-box.sh [BOX_IP=...]
set -euo pipefail

BOX_IP="${1:-136.66.196.67}"
OPERATOR_KEY="${OPERATOR_KEY:-$HOME/.ssh/oracle}"
WORK="$(mktemp -d /var/tmp/houses-seed.XXXXXX)"  # NOT /tmp: a 3.6G tmpfs
# overflows while the migration journals (638MB DB + backup + update WAL)
SEED_GS="${SEED_GS:-gs://houses-seed/latest.db}"
trap 'rm -rf "$WORK"' EXIT

echo "== 1/4 pull the live DB (read-only)"
scp -i "$OPERATOR_KEY" -o StrictHostKeyChecking=accept-new \
  "ubuntu@$BOX_IP:/opt/houses/data/houses.db" "$WORK/live.db"
ls -la "$WORK/live.db"

echo "== 2/4 migrate the COPY (the rehearsal)"
HOUSES_SCRIPTS_MAY_WRITE=1 uv run --project "$(dirname "$0")/.." \
  python scripts/backfill_person_ids.py --apply --backup --verify --db "$WORK/live.db"
sqlite3 "$WORK/live.db" "PRAGMA integrity_check;" | grep -q '^ok$'

echo "== 3/4 verify migrated keying (persons carry numeric ids)"
python3 - <<PYEOF
import sqlite3, json, zlib
con = sqlite3.connect("$WORK/live.db")
con.row_factory = sqlite3.Row
row = con.execute("SELECT result_json FROM node_results WHERE node_id='persons'").fetchone()
assert row, "persons row missing"
val = json.loads(zlib.decompress(row["result_json"]).decode())["value"]
assert all(p.get("person_id") for p in val if isinstance(p, dict)), "a person lacks person_id"
print("persons carry numeric ids:", [(p.get("name"), p.get("person_id")) for p in val])
PYEOF

echo "== 4/4 upload the seed"
gsutil cp "$WORK/live.db" "$SEED_GS"

echo "seed ready: $SEED_GS $(stat -c%s "$WORK/live.db") bytes"
echo "NEXT: gh workflow run Release --ref main -f action=provision"