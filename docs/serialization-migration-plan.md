# Serialization migration — remaining work

The one-type-per-thing migration (docs/coding-standards.md → *Boundary records*)
is complete on every surface that was converted; this is what is left. Evidence
and rulings live in [lucidlint-review-log.md](./lucidlint-review-log.md); this
file is only the plan.

**State** — verified after the 2026-10-21 pass:

- The pin moved to lucidlint master `2bc734f` (PR #43, the #42 export
  exemption). On the identical pre-migration tree that is **24 fails → 15**
  (9 `record-shape` sites clear), with **no new finding** and **no marker gone
  stale** (175 comment-suppressed on both sides).
- `make lucidlint` → **1 action = 0 fails + 1 warning** (`middle-man` on
  `serialization/__init__.py:149` — deliberate, lucidlint#44). The
  `bulk-suppression` census warning is gone, and every `record-shape` site the
  plan listed is closed — the last four by naming their wires (`MoneyWire`,
  `QuantityWire`, `RecordWire`), not by editing the tool.
- 1812 tests pass (39 dead-code tests deleted with the code they pinned);
  `make typecheck-py` → 0 errors, no baseline file; `make lint-check` and
  `ruff check scripts/` clean.
- Byte oracle: `pytest tests/unit/test_zzz_serialization_oracle.py`, then diff
  the 7 captures against `/tmp/ll-goldens/*.gold` → **identical** after masking
  the volatile fields (`api.json`, `dag_rows.json`, `node_json`,
  `node_summary`, `node_detail`, both envelopes).
- Branch `lucidlint-067-serialization`.

## Landed this pass (order items 1-4, 6's third arm, and the pin bump)

| Item | Change | Evidence |
|---|---|---|
| 1 | `CacheRequest` (method/url/params/body as state) owns the five cache seams in `houses/api_cache.py`; 8 production files and 4 test files migrated; the `latent-class` and `long-param-list` markers deleted | `api_cache.py` clean; keys byte-identical over 13 shapes (old `_make_key` vs `CacheRequest.key`); committed fixture cache still resolves |
| 2 | `ParkingRateSync` owns the run; `main` is a shell; the `:405` marker deleted | `scripts/sync_parking_rates.py` absent from the report; `houses/apcoa_scraper.py` imports and `test_car_park.py` untouched |
| 3 | `_group_slot`/`_add_delta` rebuild the group block as `dag.node.NodeValue` and write the rebuilt wire back into the slot it was read from | finding gone; the write-back lays the rebuilt wire OVER the block's own (a settled derived node's wire carries `retry_at`/`retry_count`/`stale` that `NodeValue` does not model) |
| 4 | `_GoogleClientConfig` is a `SerializableAsDict` whose one wrap serializer emits `{"web": …}`; the `_GoogleClient` TypedDict twin deleted | finding gone, no new one; `to_dict()` still returns the library's config object |
| 6 (arm 3) | `_with_coordinates` names the pair once; the `model_copy` update dict is the record's own wire, not a constant-key literal | both findings gone; no `Coordinates`/`*Wire` mirror invented |

## Remaining — 0 fails

The plan's list is closed. The last four (`serialization/__init__.py`) were
fixed by naming the wires the rule asks for — `MoneyWire` and `QuantityWire`
TypedDicts built with keyword construction, and `type RecordWire = dict[str,
Any]` for the boundary-record base (named, and still mutable: the web layer
patches a payload at its projection boundary). The tool was not edited, and the
docstrings state the contracts rather than a suppression.

What is left is **exemptions, not failures** — audited one site at a time in
[lucidlint-review-log.md](./lucidlint-review-log.md) → *The exemption audit*.
Eight were wrong (dead code kept alive by its own tests) and are deleted; the
rest stand on checkable reasons, with two marked **weak** there and left as
owner calls:

- **class-module** — **resolved**: all eight markers are gone. `signals.py` →
  `signal.py`/`slot.py`/`connection.py`; `commute_router.py` → the Google Routes
  client, its interface and its private wire records in
  `google_routes_client.py`, the options in its own module, the three returned
  projections one per module, with the wire knowledge moved out of
  `CommuteRouter` and `BusRouteNode`; and the six family files → 22 one-class
  modules named after their class. See the review log for the `duplicate-module`
  lesson that shaped the split. The work exposed two **tool gaps**, both filed as
  [lucidlint#52](https://github.com/ashbywinch/lucidlint/issues/52): the
  multi-class arm reports a module's own DI interface as a misplaced class, and
  `class_module_findings` exempts any module containing a module-level `def`, so
  a file's verdict flips when an unrelated helper moves.
- **inline-import** — **resolved**: all five sites are gone by the leaf-module
  route the 2026-08-22 decision prescribes (`dag/processor.py` for the DAG
  triangle; `houses/apcoa_rates.py` for the houses↔scripts cycle; the
  `services_provider` leaf made `houses/services.py`'s import safe to hoist).

Also resolved: `houses/nodes/bootstrap.py::bootstrap_from_row` is **deleted**
(there is no sheet import; the live `load_property_nodes_from_db`
path is now tested directly). `scripts/parse_netex_fares.py` is also
**resolved**: `NetexFareParser` → `_NetexFares` (the fares of one NeTEx file;
private, because the module's surface is `parse_netex_fares` and the records it
returns), its `zone_fares` modelled as `ZoneFares` (a keyed collection owning its
writes), so the `process-class` fail is gone and only the size warning's marker
remains.

`long-param-list` was fixed (2026-10-21): `api_fetch` takes its `FetchArgs`.

The merged #42 fix is **narrower than the issue's report**: it exempts a
`to_dict`/`to_json*`-named method whose enclosing class declares fields that
cover the literal's keys, so the 9 sites it cleared were exactly those
(`dag/node.py:267,287`, `dag/derived_node.py:1044,1050`,
`houses/model/domain.py:96,97`, `houses/nodes/commute.py:377,406`,
`houses/services.py:111`).

### Cleared in code this pass (the plan's own residue)

| Site | Shape it took |
|---|---|
| `dag/attempt.py:436,618` (#46) | `ProvenanceSources(dict[str, Provenance])` — the named keyed collection, with the stored-block parse (`from_dict`) on it |
| `dag/derived_node.py:649,811,1033` | `NodeValue` models the lifecycle keys (`retry_at`/`retry_count`/`stale`); `to_node_value`/`_wire_value` are the builders; `_safe_result`/`_build_provenance` return records, `_persist` dumps at the persistence edge |
| `houses/nodes/petrol.py:201`, `houses/nodes/commute.py` | each node's own row is a `NodeValue` subclass (`_SchoolCommuteRow`, `_CommuteSelectorRow`) named by `wire_type`; `_enrich_json` sets the record's fields |
| `houses/commute_router.py:74` | the wrap serializer's return is `Mapping[str, Any]` (the repo's own convention for wrap serializers) |
| `houses/model/domain.py:143` | `Person.to_provenance_value() -> _PersonProvenance` — the projection is a named `TypedDict` wire (the `MeasurementProvenanceValue` pattern), so `to_dict` is gone |

**The oracle caught the one regression this pass made**: `_safe_result` called
`to_node_value`, which bypassed `CommuteSelectorNode.to_json`'s value
projection, so the persisted commute row stored `_details` instead of
`details`. Fixed by making the projection a seam (`Node._wire_value`) that both
paths go through — the goldens are identical again.

### Item 7 — measured, then left for the tool

The seven class-size markers are **warnings, not fails**: deleting
`houses/commute_router.py`'s marker and re-running the gate reports
`[warn][latent-class] … a large class; split it ONLY where the partition rule
finds field-disjoint method groups` and the fail count is unchanged (12 at the
time). So the six markers are not load-bearing for the gate; they are deleted
when #47 lands, which is what removes the finding rather than the marker.

The seventh (`scripts/parse_netex_fares.py:216`) is different: it also
suppresses a **failing** `process-class` on `NetexFareParser`, whose name ends
in an enumerated process word. The rule's own three-part test is a judgement
the scanner cannot make — the domain calls this component a parser, and the
data it operates on is already mostly classes (`NetexStop`, `NetworkFare`,
`ParseResult`), with `zone_fares: dict[str, dict[str, float]]` the remaining raw
shape. Clearing it therefore means either modelling that shape and renaming the
class, or a tool exemption for a name the domain owns — an owner call, not a
mechanical edit, and the marker must stay until it is made.

## Order (for what is left)

The 4 remaining fails need the two tool rules above. Item 7's seventh needs the
owner's call on `NetexFareParser`. When the tool moves: re-run the gate first (a
bump can clear sites and stale markers at once — that is how the 9 above went),
then work what is left in the order listed.


## Acceptance for every item

1. **The gate** (missing from the earlier plan, and items 1, 2, 5, 7 are defined
   by it): `make lucidlint`, then confirm the specific finding is gone and no new
   one appeared. Warnings never fail.
2. **Byte oracle**: `pytest tests/unit/test_zzz_serialization_oracle.py` — it
   **overwrites** `/tmp/ll-goldens/*.json`; the reference copies are
   `/tmp/ll-goldens/*.gold`. Diff them with a script that, per key: parses the
   body (in `api.json` every body is a JSON **string**), masks the volatile
   fields (`freshness`, `scheduled_at`, `total_objects`/`count`, tie order in
   `top_types`), and for `dag_rows.json` compares `value_json` and the
   **decompressed** `provenance_z` (zlib; the oracle stores the *repr* of bytes,
   so `ast.literal_eval` first). `node_json`/`node_summary`/`node_detail` are
   plain JSON.
   The goldens are a snapshot of the pre-migration bytes; if `/tmp` is lost,
   re-capture from the last commit that predates the change under test.
3. **The suite**: `pytest tests/unit tests/integration -q` (1842 today).
4. **Types**: `make typecheck-py` — not bare `pyrefly check`.
5. **Hooks**: `.git/hooks/pre-commit` runs gitleaks + `make lint-check typecheck`
   for staged `*.py`; `make lint-check` covers `houses/ tests/ tools/ dag/`
   (plus frontend stylelint), **not** `scripts/` — run `ruff check scripts/` by
   hand.
6. Delete the oracle test and its goldens when the last item lands. The last
   item has NOT landed (12 blocked fails above), so both stay.

## Traps this migration has already paid for

- `@field_serializer("a")` stacked over `@field_serializer("b")` registers only
  one field, silently. One field name per decorator.
- `dataclasses.replace()` asserts a dataclass; a model copies with
  `model_copy(update=…)`.
- A model takes no positional arguments (`Provenance("label")` →
  `Provenance(label=…)` at four call sites).
- Declare fields in **wire order** — `Provenance`'s `freshness` moved because the
  old `to_dict` emitted keys in a different order than the class declared them.
- Never widen a callee's parameter to fit a caller (`Sequence` for a `list`), and
  never script an edit across call sites you have not read.
- A marker suppresses whatever finding binds at its line, and its kind name need
  not match the signal — one marker can be load-bearing for two findings (item 7).
