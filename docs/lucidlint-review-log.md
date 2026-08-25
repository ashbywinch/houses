# Lucidlint Review Log — Houses

Records every lucidlint finding we do **not** fix outright, and why. Anything
that takes time to judge or that we disagree with lands here with its verdict
(2026-08-22, first full-repo run with lucidlint 0.2.0 @ `98cf466`).

The gate: `make lucidlint` (runs `.venv/bin/lucidlint --repo . --baseline
lucidlint.json`). The baseline locks acknowledged debt; the gate fails only on
NEW actions. Per-site `# lucidlint: ignore <kind> <why>` comments beat config
ignores — a suppression without a written why is itself a finding.

## Scope decisions (user, 2026-08-22)

1. **monkeypatch (133)** — convert ALL legacy sites to DI
   (`_kwarg`/`Services`/`ContextVar`); no staging. The house testing standard
   already forbids new monkeypatch; the existing sites are the debt.
2. **magic-number (183)** — name ALL literals, prod **and** test. No
   exception for test data.
3. **inline-import (180)** — move ALL to module top. Circular-import
   avoidance is NOT a justification — the cycle is the smell; break it by
   extracting the shared interface into its own module.
4. **class-module (18)** — a module named after the domain concept but
   holding a differently-named class means the class is missing; create or
   rename. Exception: small private helper classes stay (per-site).
5. **Blanket config ignores are forbidden.** Every finding is judged on its
   merits; a category where we disagree with a subset still gets per-site
   treatment for the rest.

## Accepted / agreed findings (fixed, not logged)

- positional-literals (51), boolean-arg real sites (5), unreachable,
  noop-statement, docs-link — mechanical, fixed.
- magic-number — all 183 named as constants (prod + test) except the
  data-table/algorithm sites below.
- detached-method — `@staticmethod` conversions (14 `__init__`/`push`
  sites are false positives — see below).
- loop-pipeline — 34 comprehension conversions, 4 suppressed with whys
  (group-by accumulation / multi-branch AST walks).
- swallow — every site surfaced per coding-standards.md (log + control-flow
  exit; the pinned tool requires an exit in the except).
- suppression-no-why — 65 stale suppressions removed (pyrefly-verified),
  ~28 kept with specific whys.
- monkeypatch — all 133 converted to DI seams (`tfl_client_factory`,
  `commute_router` on `Services`; `_kwarg` seams on find_nearest,
  CommuteRouter, CarParkRegistry, map_layers, town_desc, council_tax,
  server endpoints; FastAPI dependency_overrides).
- unused-setter — 5 findings, all false positives (see below).

## Disagreed / slow — verdicts

### boolean-arg — false positive on `.get()`/`getattr()` defaults (12 sites)

The rule fires on `d.get("retryable", False)`, `getattr(p, "is_child", False)`,
`session.get("is_superuser", False)` — the boolean is the *default value* of a
lookup, not a named flag. There is no flag ambiguity and no swapped-argument
risk (single boolean parameter). Naming it (`default=False` spelled out) is
unconventional noise. Suppressed per-site with `# lucidlint: ignore
boolean-arg <why>`.

### magic-number — data-table and algorithm literals

The rule is right for code operands (thresholds, timeouts, status codes,
retry counts, conversion factors) — those got named constants. It is wrong
where the literal IS the data: statutory council-tax band ratios
(`houses/council_tax.py`), postcode bounding-box coordinates
(`houses/web/geo_utils.py` `_POSTCODE_BOUNDS`), and published-algorithm
constants (Google polyline encoding in `tools/commute/rightmove_url.py`).
Naming each coordinate/ratio would destroy the tables' readability. Those
sites get per-line `# lucidlint: ignore magic-number <why>` comments.

### complexity — engine finds no seam on 41 of 44 functions

The rule is right: these functions (cc 15–88) are too complex and we agree
they should be split. But `extract-method` previews "nothing to change" on
all but three (`tools/migrate_node_results_to_quantity.py`,
`scripts/debug_dag.py`, `houses/nodes/cutover.py` — extracted and applied).
The remaining 41 are async linear flows, repetitive push-chains, or
`main()` scripts where the tool cannot propose a self-contained seam;
hand-extraction is a designed refactor per function, not a mechanical fix.
Deferred: baselined, each needs its own split design.

### unused-setter — all 5 false positives (suppressed, not deleted)

`set_scheduler` (test-injection API, called by isolation fixtures),
`set_cache_dir` (test-injection API, called by unit conftest),
`set_after_refresh` and `set_app_mode` (both wired from
`houses/server.py` — the FastAPI lifespan), and the `Attempt.impossible`
setter (an immutability guard whose deletion would silently allow
rebinding). The rule counts prod references only; test/guard usage is
invisible to it. Per-site `# lucidlint: ignore unused-setter <why>`
comments with the reference evidence.

### detached-method — 14 `__init__`/`push` false positives

`@staticmethod` on `__init__` breaks instantiation (verified: `C(x)` raises
TypeError); the forwarding `__init__`s that call `super().__init__` must
bind `self`. `SettingsNode.push` calls `super().push()` — zero-arg `super()`
requires `self`. `DagJSONEncoder.default` and the real `compute` overrides
were converted. The 14 false positives carry per-site suppression comments.

### monkeypatch — DI conversion (done)

Agreed with the rule (testing-standards.md already forbids monkeypatch).
The `Services` container gained `tfl_client_factory` and `commute_router`
fields so transit/bus planning is injectable; `make_services()` defaults
them to safe fakes. `_scrub_secrets` gained a `secret_key_value` kwarg;
`find_nearest` gained geocode/load kwargs; `CommuteRouter` gained
constructor route fns; `RailFareNode` gained `tube_fare_fn`; the
`_kwarg`/ContextVar seams converted all 133 sites.

### record-shape — wire-format dicts are the house standard

383 findings flag dict/tuple shapes (bare `dict` return types, `dict`
literals, `tuple` returns). Per docs/coding-standards.md: "Wire formats
are the exception, explicitly: serialized payloads, config files, and
external-API request bodies use ... by design" — `to_dict`/`from_dict`,
API responses, and config shapes are sanctioned. The finding is right
where a dict shape is re-parsed in 2+ places (the standards name that
as a real smell) — those get classes. Per-site triage: wire-format seams
get a per-site `# lucidlint: ignore record-shape <why>` citing the
standard; genuine reuse-elsewhere dicts get classes.

### fakefs — tmp_path real-FS tests are the house standard

78 findings demand pyfakefs for tests that touch the real filesystem.
docs/testing-standards.md deliberately permits deterministic real-FS
tests via `tmp_path` (the standard never mentions pyfakefs). pyfakefs is
brittle with sqlite/subprocess — adopting it would make the suite worse.
The tool is wrong here; per-site `# lucidlint: ignore fakefs <why>`
comments citing the standard.

### inline-import — fixed 2026-08-23

The 4 DI-cycle sites in `houses/location.py` (get_geo_state, _geo_cache,
from_town) and `houses/commute_router.py` (_tfl_transit_commute) now import
`get_services` at module top. `houses/services_provider.py` loads
`houses.services` lazily via importlib (it is a leaf module), so the
module-level import graph is acyclic and `houses/services.py` keeps its
top-of-file imports. The geocoder functions thread a keyword-only
`services` parameter, and the default geocoder/route-planner receive the
Services container and pass it explicitly.

_(Rest populated as the passes run.)_

### long-param-list — 33 deferred (parameter objects are a design choice)

Most are the DI seams added during the monkeypatch/inline passes
(`find_nearest` geocode/load kwargs, `find_nearest_town_name` cache
kwargs, `CommuteRouter._google_routes_post` api-key/client kwargs) —
bundling them into parameter objects now would fight the DI pattern, and
the remaining legacy >5-param signatures (`__init__` wiring) take
per-function judgment on which params group into a real object.
Deferred: baselined; revisit per function when the seam settles.
(2026-08-22)

_(Rest populated as the passes run.)_

### latent-class — 10 sites deferred (2026-08-23)

The rule fires on field-disjoint method groups (a class split) and on
functions defining >=2 inner functions (a class in disguise). Every site
is a designed refactor, not a mechanical extraction — either a class
split of a core class or a stateful algorithm whose closures ARE the
state; none has a small clean seam. Deferred: baselined, each needs its
own split design.

- `dag/node.py:38` — `Node` base-class split (persistence/timestamp
  fields vs `to_json` serialization); the two groups share `__init__`
  state and the persistence contract.
- `dag/user_input_node.py:54` — `_install_third_party_schemas`, 6
  validator/serializer closures; pydantic consumes them as callables —
  a `MoneySchema`/`QuantitySchema` protocol-class redesign.
- `houses/car_park.py:70` — `CarParkRegistry` split (CSV load/persist vs
  playwright scrape); the groups share the registry state.
- `houses/nodes/total_monthly_housing_cost_node.py:192` —
  `GroupMonthlyCostNode.compute` (165-line static) with 3 closures over
  the attempt values; the apportionment needs extracting as its own
  calculator.
- `houses/commute_router.py:29` (was `houses/routing.py:29`, renamed by the class-module pass) — `CommuteRouter` split (transit/bus fallback
  vs google-routes); the DI seams (`_google_route_fn` etc.) couple the
  groups.
- `houses/tfl_client.py:658` — `_build_cost_groups` stateful accumulator
  (`_flush_transit` closure over `current_legs`/`current_mode`/
  `total_applied`); a `_CostGroupBuilder` extraction with fare logic.
- `houses/web/api_router.py:180` — `_score_from_summary`, 3 stateless
  scorer closures (no enclosing state — the proper fix is module-level
  functions, not a class).
- `houses/web/api_router.py:594` — `_person_from_dict`, 2 validation
  closures (`_money`, `_penalty`) — stateless helpers, same as above.
- `tools/commute/station_shed.py:199` — 4 free functions sharing the
  leading `Station` param; a `Station`-method/helper-class extraction
  across the module and its tests.
- `tools/commute/union.py:64` — `union_outline` contour-tracing closures
  (`pick_next`/`seg_index` over `by_start`/`used`) — a `_LoopTracer`
  extraction of the leftmost-turn walker.

One latent-class site is a verified false positive, suppressed in code
with the evidence: `scripts/enrich_with_ofsted.py:86` (the rule claims 2
inner functions in `_determine_effective_rating`; the function contains
none — only list/dict comprehensions).

### conditional-polymorphism — fixed 2026-08-23

`houses/tfl_client.py` `_format_route_summary` — the 7-arm mode chain is now
a `_LEG_LABEL_FORMATTERS` dispatch table of per-mode formatter functions
(tube/driving/bus/national-rail/overground/dlr/tram + generic fallback).
Behaviour identical; covered by `tests/unit/test_transit_route.py`.

### middle-man / special-case — per-site verdicts (2026-08-23)

middle-man (9): 8 are protocol-required wrappers suppressed with whys —
`__radd__`/`__rmul__` (reflected-operator protocol), 4 ×
`DerivedNode.compute` (`@abstractmethod`), `Expression.to_formula_lines`
(interface method walked by `to_formula()`), `Station.distance_km_to`
(live convenience). 1 removed as genuinely dead indirection:
`houses/sheets/tab.py` `Tab.get_all_values` — the whole `Tab` wrapper
was never instantiated anywhere (verified repo-wide), so the dead class
and its re-export were deleted.

special-case (4): all suppressed with whys — the None checks guard
external/wire seams (TfL API dict, registry lookup, ElementTree lookups)
or `Attempt.value_or_none()` (the domain's own absent-value marker); a
sentinel object would fight the existing layer, not simplify it.

### global-state — deferred (2026-08-23)

One module-level mutable collection needs a designed refactor and was left
for it (everything else in the category got per-site suppression comments):

- `houses/property_registry.py:5` `_registry: dict[str, PropertyNodes]` — the
  app-wide live property registry. It is genuinely mutated
  (`register_property`, `_reset`) and is not a bounded cache: it holds every
  live `PropertyNodes` object for the process. A clean fix means a class or
  a `Services`-container field threaded through every consumer — server
  seeding, `nodes/bootstrap.py`, `web/api_router.py`,
  `web/broadcaster.py`, `admin_router.py` — and rewriting the many tests
  that import and mutate `_registry` directly
  (`tests/unit/test_auth.py`, `test_card_data.py`, `test_commute_production.py`,
  `nodes/test_api.py`, `test_stamp_duty_detail.py`, …). That is a designed
  DI refactor, not a mechanical move; the module-level accessor API
  (`register_property`/`get_property`/`list_properties`) plus the `_reset`
  test seam already centralise all writers. Deferred: baselined.

### class-module — renames and suppressions (2026-08-23)

Per the user's 2026-08-22 principle (a module named after the domain concept
holds a missing class; exception for small private helpers), the 18 findings
were decided per site:

- **File renamed to the class (7):** `dag/if_then_else_node.py`
  (IfThenElseNode), `houses/commute_router.py` (CommuteRouter),
  `houses/nodes/geocode_node.py` (GeocodeNode),
  `houses/nodes/life_insurance_total_node.py` (LifeInsuranceTotalNode),
  `houses/nodes/park_and_ride_augment_node.py` (ParkAndRideAugmentNode),
  `houses/nodes/nearest_station_node.py` (NearestStationNode),
  `houses/nodes/property_nodes.py` (PropertyNodes). Imports rewritten
  across 30 files (prod + tests).
- **Suppressed with a written why (8):** the small private helpers
  `dag/persistence.py` DagJSONEncoder, `houses/api_cache.py`
  CachingTransport, `houses/comments.py` CommentEntry,
  `houses/council_tax.py` CachedVOAClient; plus the module-is-the-domain
  sites
  `houses/rightmove_scraper.py` RightmoveProperty (the module IS the
  scraper; the class is its data model), `houses/nodes/settings.py`
  SettingsNode (settings-domain module of factories + guard + one node
  class; `settings_node.py` already owns the aggregate — a file rename
  would collide), and the script/tool entry-point modules
  `scripts/parse_netex_fares.py` Station and
  `tools/commute/drive_isochrone.py` DriveDestination (module named for
  its function; the class is the small parsing/model helper).
- **Deferred (below):** `houses/config.py` Settings, `houses/geo.py`
  GeoPoint — both FIXED (2026-08-23): renamed to `houses/settings.py` /
  `houses/geopoint.py` with all importers rewritten (prod, tools, tests).

`houses/web/api_router.py` CommentBody was suppressed initially, then the
suppression was REMOVED (2026-08-23): the record-shape pass added
`DepositBreakdown` and `IsochronePaths` NamedTuples to the module, so it no
longer holds a single class and the rule stops firing — the comment became
stale and was deleted.

### class-module — fixed 2026-08-23

`houses/geo.py` → `houses/geopoint.py` (GeoPoint) and
`houses/config.py` → `houses/settings.py` (Settings) via `git mv`; every
importer rewritten (`from houses.geo import GeoPoint` →
`from houses.geopoint import GeoPoint`, `from houses.config import settings`
→ `from houses.settings import settings`) across houses/, dag/, tools/,
scripts/, tests/ and the Makefile.

### private-import — all 20 renamed public or suppressed (2026-08-23)

Every flagged import was a cross-module use of an underscore symbol. 18 are
legitimate APIs and were renamed public (definition + all importers, prod
and tests): `_get_scheduler` → `get_scheduler`,
`_extract_town` → `extract_town`, `_needs_rail_fare` → `needs_rail_fare`,
`_transit_legs` → `transit_legs`, `_geocode_address` → `geocode_address`,
`_render_leg_description` → `render_leg_description`,
`_apply_park_and_ride_to_journeys` → `apply_park_and_ride_to_journeys`,
`_SETTINGS_SOURCE_CACHE` → `SETTINGS_SOURCE_CACHE`,
`_checkpoint_path` → `checkpoint_path`,
`_dataset_description_matches` → `dataset_description_matches`,
`_js_safe_json` → `js_safe_json` (plus `_user_label` → `user_label`, the
sibling symbol on the same import line), `_outer_loop` → `outer_loop`,
`_const_range_name` → `const_range_name` — and the two symbols the renames
surfaced on the same lines: `_load_naptan_stops` → `load_naptan_stops`,
`_get_geo_state` → `get_geo_state`.

Two suppressed with whys: `houses/sheets/__init__.py` `_real_get_client`
(intra-package client-lifecycle seam — `get_client()` is the public
wrapper) and `scripts/check_restart.py` `_request_services` (DI ContextVar;
underscore names every ContextVar repo-wide — `services_provider`,
`database`, `context`, `scheduler`; the public API is `get_services()`).

### record-shape — genuine records fixed (2026-08-23)

All 20 deferred record-shape findings were fixed: the 8 NamedTuples
(JourneySummary, ParkingCostResult, SessionMint, DepositBreakdown,
IsochronePaths, GroupFigureResult, ExceptionClassification, SheetHandle)
became frozen dataclasses with every tuple-unpacking call site converted to
attribute access; the commute toolchain's coordinate tuples became `GeoPoint`
(houses/geo.py) and a shared `GridCell(row, col, lat, lon)` frozen dataclass
(tools/commute/tile.py); `TieredRate`'s band tuples became `TaxTier`;
`_oauth_states` records became `_OAuthState`; and the NeTEx stop records
became `NetexStop`. No record-shape findings remain.
