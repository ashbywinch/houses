#!/bin/sh
# /opt/houses/keepalive.sh — keep the Always-Free box from being reclaimed.
#
# Oracle reclaims Always Free instances idle for 7 days: CPU <20% AND
# network <20% AND memory <20% at the 95th percentile. This job keeps CPU
# above the line two ways, both bounded and both with value:
#
#   1. REAL work: rotate through property regenerations (fresh commutes /
#      prices — the DAG pipeline is genuine CPU + network + memory load),
#      round-robin via /opt/houses/keepalive-offset so every property is
#      refreshed over the day.
#   2. Bounded exercise: stress-ng on 3 of 4 cores for 10 minutes. 3 cores
#      busy 600s of every 1800s window = 25% average util — above the 20%
#      line with margin. The timer fires every 30 min.
#
# If the app is down (pre-cutover, or mid-flip), the regenerate phase is
# skipped — the exercise alone still clears the threshold.
set -eu

ROOT=/opt/houses
OFFSET_FILE="$ROOT/keepalive-offset"
COUNT=3                # properties per run (45 props / 24 runs/day ≈ 2/day each)
PORT=8765              # the ACTIVE side's port — recompute the LIVE data

port_of() { [ "$(cat "$ROOT/ACTIVE")" = green ] && echo 8766 || echo 8765; }
PORT=$(port_of)

# ── 1. real work: regenerate a rotating batch of properties ──────────────
SECRET=$(sudo grep '^HOUSES_SESSION_SECRET=' /etc/houses.env | head -1 | cut -d= -f2- || true)
if [ -n "$SECRET" ] && curl -fsS --max-time 5 "localhost:$PORT/health" >/dev/null 2>&1; then
  COOKIE=$(HOUSES_SESSION_SECRET="$SECRET" "$ROOT/blue/.venv/bin/python" -c '
from houses.web.auth import _make_session_cookie
print(_make_session_cookie(email="simon@example.com", name="Simon", picture="", is_superuser=True))
' 2>/dev/null || true)
  if [ -n "$COOKIE" ]; then
    RIDS=$(curl -fsS --max-time 30 -H "Cookie: session=$COOKIE" "localhost:$PORT/api/properties/all" \
      | "$ROOT/blue/.venv/bin/python" -c 'import json,sys; d=json.load(sys.stdin); ps=d.get("properties", d); print("\n".join(sorted(ps) if isinstance(ps, dict) else [p["rid"] for p in ps]))' 2>/dev/null || true)
    N=$(printf '%s\n' "$RIDS" | sed '/^$/d' | wc -l)
    if [ "$N" -gt 0 ]; then
      OFFSET=$(cat "$OFFSET_FILE" 2>/dev/null || echo 0)
      i=0
      for rid in $RIDS; do
        if [ $(( (i - OFFSET + N) % N )) -lt "$COUNT" ]; then
          echo "== keepalive: regenerate $rid"
          curl -fsS --max-time 300 -X POST -H "Cookie: session=$COOKIE" \
            -H "Content-Type: application/json" \
            -d "{\"patterns\": [\"$rid/*\"]}" \
            "localhost:$PORT/api/admin/regenerate" >/dev/null 2>&1 || true
        fi
        i=$((i + 1))
      done
      echo $(( (OFFSET + COUNT) % N )) > "$OFFSET_FILE"
    fi
  fi
fi

# ── 2. bounded exercise: 3 cores, 10 minutes ─────────────────────────────
stress-ng --cpu 3 --timeout 600s >/dev/null 2>&1 || true

echo "== keepalive done $(date -Is)"
