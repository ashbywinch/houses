# Serialization migration — remaining work

The one-type-per-thing migration (docs/coding-standards.md → *Boundary records*)
is complete on every surface that was converted; this is what is left. The
evidence for each decision is in [lucidlint-review-log.md](./lucidlint-review-log.md);
this file is only the plan.

**State**: gate `24` failing actions (all `record-shape`) + `2` warnings
(`middle-man` on `SerializableAsDict.to_dict`, `bulk-suppression` census).
1841 tests pass, pyrefly 0 errors, no baseline. Branch
`lucidlint-067-serialization`, tree clean.

## Order

1. **`CacheRequest` — the `api_cache` `data-clump` seam.**
   `_make_key`, `evict_cached`, `get_cached`, `set_cached`, `with_cache` all take
   `(method, url, params, body)`. That four-tuple is one domain thing: the
   request's identity. First step is a call-site census
   (`tfl_client`, `location`, `commute_router`, `apis/*`, `server`), because the
   record replaces four parameters at every seam. Delete the
   `api_cache.py:100` marker only when the finding stops firing.
2. **`sync_parking_rates.main` — the `assembly-class`.** `args` is threaded
   through `_select_stations` and `_filter_missing` and the run builds three
   structures; the threaded state belongs in a class. One file, no callers.
3. **`monthly_delta._group_block` → `NodeValue`.** It returns the group node's
   wire value; typing it moves its callers from `["value"]` to `.value`.
4. **`services._GoogleClientConfig`.** It adapts to `google_auth_oauthlib`'s exact
   nesting, so a model needs a whole-record serializer — one `record-shape` site
   traded for another. Decide: restructure the caller to build the nested dict,
   or leave it as an adaptation site and record it with #42.
5. **`dag/attempt.py` `sources: dict[str, Provenance]`.** Values are records; the
   tool wants a wrapper class. Filed as lucidlint#46 — leave the marker-free
   finding visible until the rule is fixed.
6. **The `#42` family (~16 sites).** Methods that return a record's dict
   (`to_json`, `to_json_value`, `to_provenance_value`, the serializer helpers in
   `serialization/`, `commute_router._Waypoint._serialize`). Blocked on
   lucidlint#42; the code is correct.
7. **Seven `god-class` markers** (`commute_router`, `tfl_client`,
   `property_nodes`, `bus_journey`, `derived_node`, `attempt`,
   `parse_netex_fares`) go when lucidlint#47 lands — probed: `seam_members: []`,
   nothing to split.

## Acceptance for every item

- Byte oracle first: `pytest tests/unit/test_zzz_serialization_oracle.py`, then
  diff against `/tmp/ll-goldens/*.gold` for the ten endpoints, the three
  `PropertyNodes` projections, both envelopes, and the 144 DAG rows
  (`value_json` and **decompressed** provenance; mask timestamps, object counts
  and tie order). Re-capture the goldens if `/tmp` is lost.
- `pytest tests/unit tests/integration -q` (1841 today), `ruff check`,
  `pyrefly check`, then the pre-commit hooks (`make lint-check` and the pyrefly
  lock) — they have caught four real defects this migration.
- Delete the oracle test and its goldens when the last item lands.

## Traps this migration has already paid for

- `@field_serializer("a")` stacked over `@field_serializer("b")` registers only
  one field, silently. One field name per decorator.
- `dataclasses.replace()` asserts a dataclass; a model copies with
  `model_copy(update=…)`.
- A model takes no positional arguments — `Provenance("label")` became
  `Provenance(label=…)` at four call sites.
- Declare fields in **wire order**: `Provenance`'s `freshness` moved because the
  old `to_dict` emitted keys in a different order than the class declared them.
- Never widen a callee's parameter to fit a caller (`Sequence` for a `list`), and
  never script an edit across call sites you have not read.
