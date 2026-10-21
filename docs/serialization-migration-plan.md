# Serialization migration — remaining work

The one-type-per-thing migration (docs/coding-standards.md → *Boundary records*)
is complete on every surface that was converted; this is what is left. Evidence
and rulings live in [lucidlint-review-log.md](./lucidlint-review-log.md); this
file is only the plan.

**State** — verified by audit (2026-10-21):

- `make lucidlint` → **26 actions = 24 `record-shape` (fail) + 2 warnings**
  (`middle-man` on `serialization/__init__.py:122`; `bulk-suppression` census).
- 1841 tests collect and pass; `make typecheck-py` (`scripts/pyrefly-lock.py
  check --pyrefly-config pyrefly.toml`) → 0 baselined errors, no baseline file.
- Branch `lucidlint-067-serialization`, tree clean.

The 24 `record-shape` sites, in full (the Order below accounts for 4 of them):

```
dag/attempt.py:436,618          dag/derived_node.py:649,811,1033,1044,1050
dag/node.py:267,287             houses/commute_router.py:74
houses/model/domain.py:96,97,143  houses/nodes/commute.py:377,406
houses/nodes/petrol.py:201      houses/rightmove_scraper.py:470,497
houses/services.py:111          houses/web/monthly_delta.py:37
serialization/__init__.py:50,70,75,122
```

## Order

### 1. The cache seam: a class, not a record beside free functions

`houses/api_cache.py`. `_make_key` (109), `get_cached` (123), `set_cached`
(136), `evict_cached` (181) and `with_cache` (187) all take
`(method, url, params, body)`; the tool reports `data-clump` with
`seam_members: ['body', 'method', 'params', 'url']`.

**The end state is not "a `CacheRequest` record plus the five functions".** That
was probed: it clears `data-clump` and replaces it with a **failing** `strewing`
finding — *"5 free functions all take 'CacheRequest' as their first argument — a
'CacheRequest' class is missing, and these functions are its methods … fix:
lucidlint fix --kind extract-class"*. The class owns the methods; the four
parameters become its state.

- Census, scoped to this checkout (`.kilo/worktrees/exclusive-pincushion` is a
  second checkout and double-counts ~50 hits): `api_cache.py` itself (9 internal
  calls: `126,140,183` `_make_key`; `211,215` in `with_cache`; `252,263,279,284`
  in `CachingTransport`), then **14 production callers** —
  `tfl_client.py:431,858,903,905,917`; `commute_router.py:268,280`;
  `location.py:386,396`; `council_tax.py:359,374`; `epc.py:147`;
  `apigw.py:263`; `town_desc.py:131` — plus **21 positional calls** in
  `tests/unit/test_tfl_cache.py` (`67,77,85,101,116…415`) and
  `tests/unit/test_routing.py:673`. `houses/apis/*` and `houses/server.py` have
  **zero** callers (the earlier plan named them wrongly).
- `location.py:75-76` declares the DI seams **by signature**
  (`get_cached_fn`/`set_cached_fn`) — those annotations change with the shape.
  `commute_router.py:51,214,227` and `town_desc.py:77` use `Callable[..., …]`
  and survive.
- **Open decision**: `body` is `str | None` in four of the five functions but a
  `SerializableAsDict | Mapping | None` in `with_cache`, which serialises it
  before keying. Pick one: serialise at the caller, or let the class serialise a
  record body (which moves the "serialised once at the cache-key edge" seam).
- Second marker to delete: `long-param-list` on `with_cache` goes **stale** once
  the parameters collapse (probed). The module docstring's example
  (`api_cache.py:11-16`, `key = ("GET", url, params, None)`) stops type-checking
  and needs rewriting.
- Done when: `data-clump`, `strewing` and the stale `long-param-list` marker are
  all absent from `make lucidlint`.

### 2. `scripts/sync_parking_rates.py` — the assembly class

`main` (406) threads `args` through exactly two functions —
`_select_stations(args, all_stations)` (386) and `_filter_missing(args, stations,
existing)` (393) — and builds three structures: `all_stations`/`stations`
(412-416), `existing` (415), `rate_map` (419).

- Open: the class name, its fields, and where it lives (coding-standards:97 "each
  class in its own module"); how `main`'s body (406-449) is rewritten; whether
  the Playwright loop, `_process_station` and `write_rates` move with it.
- The extraction must leave `houses/apcoa_scraper.py`'s imports
  (`city_slugs`, `extract_daily_rate_from_tariff`, `make_slug`) and
  `tests/unit/test_car_park.py`'s ~10 tests on
  `extract_daily_rate_from_tariff` untouched.
- The marker at `:405` records the **opposite** verdict ("thin CLI shell … no
  reuse axis"); this plan overrules it — delete it with the change.
- No test names `main`, `_select_stations` or `_filter_missing`, so the only
  observable is the finding disappearing from the gate. Note `make lint-check`
  does not cover `scripts/` — run bare `ruff check scripts/`.

### 3. `houses/web/monthly_delta.py:37` — `_group_block` → `NodeValue`

- One production caller (`attach`, `:68-73`) plus a **shadow copy of the function
  in `tests/unit/test_monthly_delta.py:24`** used at `:260,:272` — update both.
- The conflict to resolve first: `_group_block` casts the payload's
  already-serialised wire (`cast(dict, group)`) and its docstring (`:38-42`)
  says the boundary *asserts* the shape because *"a rebuilt copy would swallow
  the caller's delta"*. `NodeValue` therefore means either
  `NodeValue.model_validate(group)` — the rebuild the docstring forbids — or
  changing how `houses/nodes/property_nodes.py:634,672` puts
  `group_monthly_cost=await …to_json_value()` into the payload.
- The caller **mutates** the block: `group["value"] = {**value, "delta_vs_home":
  delta_wire}` (`:73`) becomes attribute access plus re-serialisation into
  `summary`; detail payloads nest it under `affordability` (`:46`).
- Covered implicitly by the oracle (`api.json`, `node_json.json` capture the
  group block).

### 4. `houses/services.py:111` — `_GoogleClientConfig`: a decision, then a change

- `to_dict()` returns `{"web": _GoogleClient(...)}` (`_GoogleClient` is a
  `TypedDict` at `:90`); two call sites, both in the module —
  `_default_oauth_authorization_url` (266) and `_default_oauth_exchange_code`
  (285), both feeding `Flow.from_client_config(...)`.
- The doctrine already answers this: `coding-standards.md:86-91` says a
  whole-record `@model_serializer(mode="wrap")` **is** warranted when the
  structure changes rather than a key. So the honest choice is: a wrap serializer
  on a model (sanctioned, and it clears the finding), or restructure the two
  callers to build the nested dict and delete the record. Pick one here rather
  than leaving it open.

### 5. `dag/attempt.py:436,618` — the `sources` map (lucidlint#46)

- Two sites, both **method parameters** (`Attempt.to_provenance(..., sources:
  dict[str, Provenance] | None = None)`, `Provenance.composite(cls, label,
  sources, …)`) — not the field at `:580`, which the tool does not flag.
- Call sites if a wrapper is ever adopted: `derived_node.py:874` (the only
  production caller passing a dict), `attempt.py:455,620,656`, and
  `tests/unit/dag/test_attempt.py:142,164`.
- The rule wants a wrapper class; the values are records, so this is a rule
  divergence — leave visible until #46 is answered. #46 must be recorded in the
  review log's *Tool bugs filed* section (it is not there yet).

### 6. The `#42` family — 20 sites, not ~16

- `dag/derived_node.py:649,811,1033,1044,1050`; `dag/node.py:267,287`;
  `houses/nodes/commute.py:377,406`; `houses/nodes/petrol.py:201`;
  `houses/model/domain.py:96,97,143`; `houses/commute_router.py:74`;
  `serialization/__init__.py:50,70,75,122`.
- Blocked on lucidlint#42 (issue state is not in the repo, so there is no
  unblock signal — check the issue, or re-run the gate after a lucidlint bump).
- **Two sites are a third arm and need their own item**: 
  `houses/rightmove_scraper.py:470,497` are
  `extracted.model_copy(update={"latitude": …, "longitude": …})` — constant-key
  *update* dicts for `_PropertyExtract`, not a method returning a record's dict
  and not inside a serializer. Decide: give the pair a name (`Coordinates`) and
  pass it, or fold it into #42's report.

### 7. The class-signal markers (lucidlint#47)

- Six of the seven go when #47 lands: `commute_router.py:285`,
  `tfl_client.py:434`, `nodes/property_nodes.py:205`, `bus_journey.py:111`,
  `dag/derived_node.py:335`, `dag/attempt.py:283` — all probed `god-class`,
  `seam_members: []`.
- **The seventh does not**: `scripts/parse_netex_fares.py:216` also suppresses a
  **failing** `process-class` finding on `NetexFareParser` (proved by restoring
  only that marker: 34 actions, no `process-class`). Deleting it re-exposes a
  fail that #47 does not cover, and moves the gate's failing count from 24 to 25.
  Resolve that `process-class` finding first.
- #47 must be recorded in the review log's *Tool bugs filed* section.

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
3. **The suite**: `pytest tests/unit tests/integration -q` (1841 today).
4. **Types**: `make typecheck-py` — not bare `pyrefly check`.
5. **Hooks**: `.git/hooks/pre-commit` runs gitleaks + `make lint-check typecheck`
   for staged `*.py`; `make lint-check` covers `houses/ tests/ tools/ dag/`
   (plus frontend stylelint), **not** `scripts/` — run `ruff check scripts/` by
   hand.
6. Delete the oracle test and its goldens when the last item lands.

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
