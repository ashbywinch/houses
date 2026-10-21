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
- `make lucidlint` → **13 actions = 12 `record-shape` (fail) + 1 warning**
  (`middle-man` on `serialization/__init__.py:122` — deliberate, lucidlint#44).
  The `bulk-suppression` census warning is gone: no signal is suppressed at
  ≥10 sites any more (`latent-class` 8, `record-shape` 7).
- 1842 tests pass; `make typecheck-py` → 0 errors, no baseline file;
  `make lint-check` and `ruff check scripts/` clean.
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

## Remaining — 12 fails, every one blocked on a tool bug

Left visible, no marker: the code is correct and a marker here is the excuse
pattern the review log exists to prevent.

```
dag/attempt.py:436,618              #46 — a typed map of records as a parameter
dag/derived_node.py:649,811,1033    #42 residue — a -> dict helper, not an export edge
houses/commute_router.py:74         #42 residue — a wrap serializer's -> dict
houses/model/domain.py:143          #42 residue — to_provenance_value is not an export name
houses/nodes/petrol.py:201          #42 residue — the _enrich_json parameter arm
serialization/__init__.py:50,70,75,122  #42 residue — PlainSerializer targets + the base's to_dict
```

The merged #42 fix is **narrower than the issue's report**: it exempts a
`to_dict`/`to_json*`-named method whose enclosing class declares fields that
cover the literal's keys, so the 9 sites it cleared are exactly those
(`dag/node.py:267,287`, `dag/derived_node.py:1044,1050`,
`houses/model/domain.py:96,97`, `houses/nodes/commute.py:377,406`,
`houses/services.py:111`). Everything else in the family still fires; #42 stays
open upstream for them.

### Blocked, with the evidence

- **Item 5** (`dag/attempt.py:436,618`) — lucidlint#46 is open and no fix is on
  master: `git log 5c893cbc..2bc734f` is the #42 fix plus two
  docs-enumeration commits. The values are records, so the rule's collection
  class has no operations to own; the two sites stay visible.
- **Item 7** — lucidlint#47 is open, so the six class-size markers stay. The
  seventh (`scripts/parse_netex_fares.py:216`) is **load-bearing for a failing**
  `process-class` on `NetexFareParser`: deleting it re-exposes a fail that #47
  does not cover. Clearing that finding means modelling the NeTEx parse data as
  classes first (the rule's own three-part test) — a slice of its own, and
  pointless to land before #47, because the marker still has to suppress the
  size signal.
- **Item 6's residue** — the 12 fails listed above.

## Order (for what is left)

Nothing here is actionable until the tool moves. When it does: re-run the gate
first (a bump can clear sites and stale markers at once — that is how the 9
above went), then work what is left in the order the residue above is listed.


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
