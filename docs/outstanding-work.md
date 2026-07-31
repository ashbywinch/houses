# Outstanding Work

## (1) Dynamic Dependencies in the DAG — RESOLVED

RailFareNode now wrapped in an `IfThenElseNode` with `_needs_rail_fare` condition, driven by `_get_active_deps()` — the built-in conditional-dependency mechanism.

## (2) Audit All Restored Tests — RESOLVED

All restored tests verified against original intent. `bus_result` removed from `CommuteSelectorNode` (dead parameter — never passed in production, unused in `compute()`). Tests updated. 841 passing, 0 failing.

## (3) Adult Commutes Have Zero Cost — RESOLVED

**Root cause:** stale persisted DAG results. The commute node computed before rail_fare settled; `_is_stale()` didn't flag recompute because rail_fare's timestamp was older than commute's. The merge logic (inside `CommuteSelectorNode.compute()`) had no independent freshness tracking.

### Fixed this session

- **Removed `bus_result`** from `CommuteSelectorNode` — dead code; its position in `_get_active_deps()` caused a parameter-ordering mismatch (bus_result landed in the `walk` slot when present).
- **Added `test_walk_selected_when_fastest`** — verifies walk/drive deps arrive at correct `compute()` positions.
- **Cleared stale DB results** for 87811437 (Simon/Pimlico, Lorena/Aldgate) — both now show `daily_cost=GBP 100.00`.

### Remaining architecture concern

The rail-fare merge (applying NR fare to selected transit) lives inside `CommuteSelectorNode.compute()` — invisible to provenance, no independent freshness tracking. Should be its own node (`MergeRailFareNode`) so provenance captures inputs/outputs and staleness is tracked independently — prevents recurrence.

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
| `tests/unit/nodes/test_commute.py` | Selector, dynamic deps, rail fare merge tests |
