# Outstanding Work

## (1) Dynamic Dependencies in the DAG

`RailFareNode` is only needed as a dependency of `CommuteSelectorNode` when the TfL commute cost turns out to be £0. But `DerivedNode` deps are static (set once in `__init__`). There's no mechanism for a node to say "I need this dep only if condition X is true, and I need to be re-queued when the dep becomes available."

Current workaround: connect `RailFareNode.changed` to `CommuteSelectorNode._on_dep_changed` manually (not through the deps tuple), and have `compute()` call `rail_fare_node.attempt()` conditionally.

This is a bodge. The right fix is to add a first-class mechanism to the DAG for optional/conditional dependencies.

## (2) Audit All Restored Tests

9 test files were restored from git commit `52f3fb1^` and updated to work with the new DAG-based code. Some may have had their assertions weakened to pass with broken code instead of asserting correct behavior. An earlier audit pass fixed some of these, but there may be more.

Each restored test must be checked against the ORIGINAL test (from `52f3fb1^`) to verify:
- The same behavior is being tested
- The expected values match the original
- Any test changed to pass with broken code is fixed to test correct behavior

The restored files:
- `tests/unit/test_enricher.py` (21 tests) — commute pipeline tests
- `tests/unit/test_card_data.py` (31 tests) — commute display, scoring
- `tests/unit/test_enrichment_flow.py` (16 tests) — DAG flow, API
- `tests/unit/test_nodes.py` (4 tests) — address/geo serialization
- `tests/unit/test_persistence.py` (18 tests) — dag/persistence
- `tests/unit/test_resolver.py` (9 tests) — DerivedNode
- `tests/unit/test_sheet_update.py` (4 tests) — Row mapping
- `tests/integration/test_enricher.py` (5 tests) — commute pipeline
- `tests/integration/test_enricher_geocode.py` (8 tests) — GeocodeNode

## Current test count

576 tests passing, 0 failing (as of commit e5409bf).
