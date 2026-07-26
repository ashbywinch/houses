# Outstanding Work

## (1) Dynamic Dependencies in the DAG

RESOLVED in this session. RailFareNode is now wrapped in an `IfThenElseNode` with
`_needs_rail_fare` condition, driven by `_get_active_deps()` — the built-in DAG
mechanism for conditional dependencies that was added earlier. No workaround needed.

## (2) Audit All Restored Tests

RESOLVED. All restored tests verified against the original intent. The
`bus_result` parameter was removed from `CommuteSelectorNode` (it was a dead
parameter — never passed in production, not used in `compute()`). Tests were
updated accordingly. 841 tests passing, 0 failing.

## (3) Adult Commutes Have Zero Cost

RESOLVED. Root cause was **stale persisted DAG results**: the commute node was
computed before the rail_fare node settled, and `_is_stale()` didn't flag it
for recomputation because the rail_fare's timestamp was older than the
commute's. The merge logic (hidden inside `CommuteSelectorNode.compute()`)
had no independent freshness tracking.

### What was fixed this session

- **Removed `bus_result` parameter** from `CommuteSelectorNode` — it was dead
  code (never passed in production, not used in `compute()`), and its position
  in `_get_active_deps()` caused a parameter-ordering mismatch where
  `bus_result` landed in the `walk` slot when present.
- **Added test** `test_walk_selected_when_fastest` — verifies walk/drive deps
  arrive at the correct `compute()` positions.
- **Cleared stale DB results** for properties 87811437 (Simon/Pimlico and
  Lorena/Aldgate) — both now correctly show `daily_cost=GBP 100.00`.

### Remaining architecture concern

The merge logic (applying rail_fare cost to selected transit) lives inside
`CommuteSelectorNode.compute()` — invisible to provenance and lacking
independent freshness tracking. It should be its own DAG node
(`MergeRailFareNode`) so provenance captures inputs/outputs and staleness is
tracked independently. This would prevent the stale-data scenario from
recurring.

### How to clear stale results

```bash
sqlite3 data/houses.db "DELETE FROM node_results WHERE node_id LIKE '<RID>/%/<NODE_ID>';"
touch houses/server.py
sleep 5
```

### Relevant files

| File | Role |
|------|------|
| `houses/nodes/commute.py` | `CommuteSelectorNode` (fixed: removed `bus_result`) |
| `houses/nodes/rail_fare_node.py` | NR fare computation |
| `dag/derived_node.py` | `build_provenance()`, `_is_stale()` |
| `tests/unit/nodes/test_commute.py` | Tests for selector, dynamic deps, rail fare merge |
