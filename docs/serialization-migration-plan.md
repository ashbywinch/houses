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
- `make lucidlint` → **5 actions = 4 `record-shape` (fail) + 1 warning**
  (`middle-man` on `serialization/__init__.py:122` — deliberate, lucidlint#44).
  All 4 fails are in `serialization/__init__.py`; the `bulk-suppression` census
  warning is gone (no signal suppressed at ≥10 sites).
- 1843 tests pass; `make typecheck-py` → 0 errors, no baseline file;
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

## Remaining — 4 fails, all in `serialization/__init__.py`

Left visible, no marker: the code is correct and a marker here is the excuse
pattern the review log exists to prevent.

```
serialization/__init__.py:50:12   _money_wire's {amount, currency} literal
serialization/__init__.py:70      _quantity_wire returns dict[str, Any]
serialization/__init__.py:75:12   _quantity_wire's {value, unit} literal
serialization/__init__.py:122     SerializableAsDict.to_dict returns dict[str, Any]
```

Both are **rule-versus-doctrine divergences, not code defects**, so clearing
them means moving the tool's rules:

1. **The literal arm fires inside a field serializer.** `_money_wire` and
   `_quantity_wire` are `PlainSerializer` targets; the doctrine is explicit
   that third-party types (`money.Money`, `pint.Quantity`) get a serialiser on
   the field that carries them, "never a class beside them" — and `MoneyJson`
   is named in the ruling as a class that was **deleted**. The tool's advice
   ("make a class with these fields") is the one shape the doctrine forbids.
2. **The return arm fires on the boundary-record base.** `to_dict` on
   `SerializableAsDict` delegates to `self.model_dump(...)`; the class declares
   no fields by design, so the export exemption (#42) does not reach it — the
   fix's own commit message keeps this case firing on purpose ("a to_dict on a
   class declaring no field"). Every subclass that declares fields names the
   shape, so the base is the mechanism, not a lost shape.

Proposed tool rules (both narrow, both implementable in the scanner):
exempt a literal inside a function referenced by `PlainSerializer` /
`BeforeValidator` / `AfterValidator` or decorated `field_serializer` /
`model_serializer` (a serialization edge owns the shape it emits); and exempt
a `to_dict`/`to_json*` method whose body has no dict literal and returns a call
on `self` (the base delegates to its own `model_dump` — the shape is named by
the subclasses).

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
