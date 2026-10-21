# Lucidlint Review Log — Houses

Records every lucidlint finding we did **not** fix outright, and why — plus
what lucidlint missed and where the tool cost us time. Current state:
**lucidlint 0.6.7 @`2bc734f` — 12 open `record-shape` (fail) + 1 `middle-man`
warning**, all deliberately visible: the tool bugs in
[lucidlint#42 / #46](#tool-bugs-filed-open) — #42's merged fix is narrower than
its report, see the 2026-10-21 section below — plus the DAG's deliberately
dict-like node values; four `duplicate-module` markers stand on their recorded
ruling in
[§Serialisation migration](#serialisation-migration-owner-ruling-2026-10-17-one-type-per-thing).
Before that: the 0.6.7 sweep (2026-10-08) and the 0.4.0 sweep (2026-08-30,
**0 open actions, empty baseline**). Every remaining diagnostic carries a per-site
`# lucidlint: ignore <kind> <why>` comment; a suppression without a written
why is itself a finding. The 0.2.0 suppression census (record-shape 408,
fakefs 77, broad-except 72, magic-number 54, global-state 33, detached-method
16, boolean-arg 12, middle-man 8, class-module 7, unused-setter 5,
special-case 4, loop-pipeline 4, private-import 2, swallow 1) was re-audited
during the 0.4.0 sweep — 80 markers had gone stale and were removed.

The gate: `make lucidlint` (`.venv/bin/lucidlint --repo . --baseline
lucidlint.json`). Warnings never fail.

## Scope decisions (user, 2026-08-22)

1. **monkeypatch** — convert ALL legacy sites to DI; no staging.
2. **magic-number** — name ALL literals, prod **and** test.
3. **inline-import** — move ALL to module top; circular-import avoidance is
   not a justification — break cycles by interface extraction.
4. **class-module** — a domain-named module holding a differently-named class
   means the class is missing; create or rename. Exception: small private
   helpers (per-site).
5. **No blanket config ignores** — per-site treatment only.
6. **No deferrals without user agreement** (added 2026-08-23) — "deferred"
   turned out to mean *fixed later in this same effort*; nothing remains
   baselined or deferred.

## Everything fixed

All 19 finding kinds reached zero. Highlights:

| Kind | Count | Outcome |
|---|---|---|
| record-shape | 383 | NamedTuples → frozen dataclasses; TaxTier/_OAuthState/NetexStop/GridCell extracted; coordinate tuples → GeoPoint; wire-format dicts suppressed with the standard citation |
| magic-number | 183 | all named as constants, prod + test |
| inline-import | 188 | moved to module top; 2 real cycles broken by interface extraction |
| monkeypatch | 133 | all converted to DI seams |
| positional-literals / boolean-arg / unreachable / noop-statement / docs-link | 60 | mechanical fixes |
| detached-method | 97 | `@staticmethod` conversions |
| swallow | 74 | surfaced (log + control-flow exit) |
| suppression-no-why | 74 | 65 stale suppressions removed (pyrefly-verified), rest carry whys |
| complexity | 44 | engine seams where proposed; hand-designed splits elsewhere |
| long-param-list | 33 | domain parameter objects (`HousingCostConfig`, `TransitOptions`, `CommuteSelectorOptions`, `SchoolLookupOptions`, `ReverseGeocodeOptions`, `WalkabilityFns`) |
| latent-class | 10+7 | Node persistence mixin, `_CostGroupBuilder`, registry splits, schema classes, `_GroupCostCalculator` |
| loop-pipeline / unused-setter / noqa / global-state / class-module / private-import / conditional-polymorphism / middle-man / special-case | rest | fixed or per-site verdicts below |

## Per-site verdicts (disagreed with lucidlint, evidence recorded)

### boolean-arg — false positive on `.get()`/`getattr()` defaults

The rule fires on `d.get("retryable", False)` — the boolean is the *default
value* of a lookup, not a named flag. No flag ambiguity, no swapped-argument
risk. Naming it is unconventional noise. 12 sites suppressed with why.

### magic-number — data tables and algorithm literals

The rule is right for code operands (thresholds, timeouts, status codes,
retry counts) — those got constants. It is wrong where the literal IS the
data: statutory council-tax band ratios (`houses/council_tax.py`),
postcode bounding-box coordinates (`houses/web/geo_utils.py`
`_POSTCODE_BOUNDS`), Google polyline encoding constants
(`tools/commute/rightmove_url.py`). Naming each would destroy the tables'
readability. Per-line suppressions citing the standard.

### unused-setter — all 5 false positives

`set_scheduler`/`set_cache_dir` are test-injection APIs (called from
isolation fixtures/conftest); `set_after_refresh`/`set_app_mode` are wired
from `houses/server.py` (the FastAPI lifespan); `Attempt.impossible`'s
setter is an immutability guard. The rule counts prod references only —
test/guard usage is invisible to it. Suppressed with reference evidence.

### detached-method — 14 `__init__`/`push` false positives

`@staticmethod` on `__init__` breaks instantiation; forwarding `__init__`s
must bind `self`; `SettingsNode.push` calls `super().push()` which requires
a bound self. Real conversions were done (DagJSONEncoder.default kept as an
instance method because `super().default(o)` needs self). The false
positives carry suppression comments with the reason.

### fakefs — tmp_path real-FS tests are the house standard

docs/testing-standards.md deliberately permits deterministic real-FS tests
via `tmp_path` and never mentions pyfakefs; pyfakefs is brittle with
sqlite/subprocess. The tool's prescription would make the suite worse.
77 per-site suppressions citing the standard.

### record-shape — wire-format dicts are the house standard

coding-standards.md: "Wire formats are the exception, explicitly:
serialized payloads, config files, and external-API request bodies use ...
by design." to_dict/from_dict, API responses, config shapes suppressed with
the citation; genuine re-parsed records became classes
(`ExceptionClassification`, `TaxTier`, `_OAuthState`, `NetexStop`,
`GridCell`, `JourneySummary`, `DepositBreakdown`, `IsochronePaths`,
`SessionMint`, `SheetHandle`, `GroupFigureResult`, …).

### broad-except / global-state — boundary catches and bounded state

Boundary/fallback catches (cache-write must never break a request;
background-loop survives node failure) suppressed with whys; 4 tightened
to specific exceptions where obvious. Bounded caches
(`_CODE_VERSION_CACHE`, TypeAdapter cache) and test-seam globals
(`DB_PATH` swap for in-memory DB) suppressed; one site
(`houses/sheets/tab.py`) removed as dead.

### class-module — renames and helper exceptions

Renamed to match their single class (imports rewritten across 30 files):
if_then_else_node, commute_router, geocode_node,
life_insurance_total_node, park_and_ride_augment_node,
nearest_station_node, property_nodes, settings (was config), geopoint
(was geo). Small private helpers suppressed with whys: DagJSONEncoder,
CachingTransport, CommentEntry, CachedVOAClient, StationControl,
DriveDestination, CommuteRouterLike protocol.

### middle-man / special-case

Middle-man: reflected-operator protocol (`__radd__`/`__rmul__`),
`@abstractmethod` compute overrides, interface methods — suppressed with
whys; one genuinely dead wrapper (`Tab.get_all_values` + its never-used
class) deleted. Special-case: None checks guard external/wire seams;
suppressed with whys.

### private-import — renamed public

All 20 were cross-module uses of underscore symbols: renamed public
(get_scheduler, extract_town, needs_rail_fare, transit_legs,
geocode_address, render_leg_description, apply_park_and_ride_to_journeys,
SETTINGS_SOURCE_CACHE, checkpoint_path, dataset_description_matches,
js_safe_json, user_label, outer_loop, const_range_name, get_geo_state)
with all importers updated; 2 intra-package sites suppressed.

## What lucidlint missed (found by other gates during this effort)

The code-health sweep caught real bugs the tool has no rule for. These are
the argument for keeping the other gates sharp alongside lucidlint:

1. **Sheets API typo** — `houses/sheets/row.py` wrote headers with
   `value_input_option="USER_ENTEred"` (invalid enum → API 400 on every
   empty-sheet headers write). Caught by pyrefly bad-argument-type against
   the gspread StrEnum.
2. **Money double-wrap** — `houses/nodes/cutover.py` built
   `Money(str(already_Money), "GBP")` after another pass made the price a
   Money → Decimal parse crash on `"GBP 800,000.00"`. Found when the
   upsert_property split ran test_server.
3. **Missing return** — `tools/migrate_comments.py get_property_rids`
   computed the RID list and discarded it; the caller did `len(None)`.
   Caught by ruff F841.
4. **Missing import** — `houses/schools.py` used `logging.getLogger` with no
   `import logging` (NameError at import). Found during test-call-site fixes.
5. **Dropped call stages** — a long-param refactor silently deleted the
   Stage-1/Stage-2 fare-match calls inside `fares_for_stops`, so name-based
   lookups always returned `{}`; only the Stage-3 comment survived. Caught
   by test_bus_fares assertions.
6. **Dead wrapper class** — `houses/sheets/tab.py` `Tab` was never
   instantiated anywhere; found via the middle-man triage.
7. **Latent NameError** — capture_dom's device-flow poll read `r.json()`
   outside the try that binds `r`; found when splitting `login`.
8. **Type regressions** — ~150 pyrefly errors introduced by the concurrent
   refactors themselves (NoneType attribute access, tuple unpacking of
   dataclasses, invariant-typed branch params): fixed by the type-fix
   passes, proving the refactors need the typecheck gate run continuously,
   not at the end.

## Lucidlint engine quirks we hit (cost time; know these)

Documented so the next run doesn't re-derive them:

1. **swallow demands a control-flow exit, not a log.** The pinned scanner
   accepts only return/raise/break/continue/sys.exit (or mutation of a
   returned name) inside the except. A debug-log-only except stays flagged.
   Verified empirically and in `scanner/src/checks.rs`.
2. **extract-method mis-binds `--name` on multi-literal lines**: it replaced
   a ternary's *result* instead of the threshold constant, and set new
   constants to the FIRST literal on the line rather than the flagged one.
   Always review the applied diff; hand-fix when it mis-binds.
3. **positional-literals `--params` applies to the wrong callee on nested
   lines**: `Station(..., GeoPoint(0, 0))` got GeoPoint keyworded with
   Money's parameter names (`GeoPoint(amount=0, currency=0)`).
4. **record-shape comments re-anchor to the outermost literal**: when a
   flagged dict is nested inside a call, the marker may bind to the outer
   expression instead — verify placement after each move.
5. **docs-undiscoverable reports ONE undiscoverable doc per run** — fixing
   it reveals the next; enumerate with a local BFS replica instead of
   iterating scans.
6. **inline-import vs the services_provider lazy-import idiom**: top-level
   `get_services` imports cycle through services → module → back. The fix
   that held: make `services_provider` a leaf (importlib lazy load of
   services) and thread a keyword-only `services` parameter through
   location/routing functions.
7. **missing-override-decorator cannot be satisfied on class-level attribute
   assignments** (`provenance_source_type = SourceType.API`): Python cannot
   decorate an assignment. Resolved by declaring the base attribute with an
   annotated default on the base class; subclass reassignments remain
   baselined-by-design (pyrefly baseline documents them).
8. **magic-number counts operands, not occurrences of meaning**: naming one
   literal reveals the next on the same line — chase cascades to zero in
   one pass.
9. **The `--params` flag is required for external callees** of
   positional-literals; same-file callees resolve automatically but nested
   callees may win over the flagged one (see quirk 3).

## Baselines

- `lucidlint.json`: **empty** — zero acknowledged debt.
- `.pyrefly-baseline.json`: **empty** (`"errors": []`) — all 235 baselined
  errors eliminated (174 missing-override-decorator were all real methods
  that got `@override`; the rest were test-fixture type fixes, None-guards,
  and proper Money/httpx constructions; 6 deliberately-wrong fixtures kept
  with written `# type: ignore` whys because the assertion IS the wrong
  type). Regenerate only after deliberate diagnostic changes.

## Warnings

All lucidlint warnings (magic-number, broad-except, middle-man,
special-case, detached-method, positional-literals, loop-pipeline,
conditional-polymorphism) are fixed or carry per-site whys — the gate
reports 0 actions of any severity. The pytest suite runs with **zero
warnings** after converting the starlette per-request `cookies=` deprecation
sites to `client.cookies.set(...)`.

## 0.4.0 sweep (2026-08-30)

Repin `98cf466` (0.2.0) → `f8cbbc8` (**0.4.0**). The new detectors surfaced
**587 actions (458 fail-severity)** on a repo the 0.2.0 sweep had driven to
zero — all from deeper descent and new rules, not from code decay. Doctrine
held: fix what we agree with, suppress the rest per-site with cited whys, no
baseline. Disposition: fixed = annotations, extractions, dead-code deletion;
suppressed = wire formats, keyed collections, deliberate parallel structure.

### Rule improvements vindicated (0.4.0 fixed 0.2.0 false-positive classes)

- **magic-number data-table exemption** (≥3 same-kind numeric siblings) —
  obsoleted the 0.2.0 per-site data-table verdicts (council_tax BAND_RATIOS,
  postcode bounds): the suppressions went stale because the rule now agrees
  with us.
- **detached-method trivial-stub exemption** (protocol/abstract bodies).
- **boolean-arg `.get()`/`getattr()` default fix** — the 12 false positives
  from the 0.2.0 log now pass without markers.

### Per-site verdicts (disagreed with 0.4.0)

- **bare-record-collection over-fires on keyed collections** — `dict[str, X]`
  lookup tables (grid lattice coords, flow-id maps, `dict[str, Attempt]`
  buckets) are keyed collections, not records. Suppressed with
  "keyed collection, not a record" + flagged as an over-fire candidate.
- **wire-format descent is too eager** — OSRM/ORS request bodies, Leaflet
  layer configs, CSV row shapes and API envelopes re-fired despite the house
  wire-format standard; suppressed with the coding-standards citation.
- **large-class/latent-class on cohesive DI surfaces** — CommuteRouter (17
  methods, one wiring contract), BusJourneyRegistry (12 methods, one
  `_data` store), DerivedNode lifecycle: no field-disjoint partition exists;
  suppressed, not split.
- **operator triads** (Measurement `__add__`/`__sub__`/`__mul__`) —
  extracted `_binop` (docstring already claimed shared algebra); 100%-identical
  cross-file twins (`_fail`, `_same_payload`, `_existing_payload`) — extracted
  to `tools/commute/payload_checks.py`.

### Engine quirks (cost real time; verified empirically or in scanner internals)

1. **`fix` behavior is per-kind, and SILENT when it applies.** Verified on a
   seeded probe post-sweep:
   - **APPLIES immediately** (one-line confirmation, no diff, no preview):
     `stale-suppression`, `magic-number --name <CONST>` (inserts the constant
     at module level + rewrites the usage — verified correct),
     `duplicate-block` (deletes the second copy).
   - **PRESCRIBES only** (prints instruction text, edits nothing):
    `undeclared-attribute`, `positional-literals` (no-op unless `--params`
    resolves, and it mis-binds nested callees — see the 0.2.0 quirk list
    above, items 3 and 9).
   You cannot tell apply-from-prescribe by the output text — always
   `git diff` (or hash-check) after running `fix`.
   **Sweep cost of getting this wrong:** the agent policy asserted
   "prescription-only" for all kinds after a two-kind probe without hash
   checks, so ~90 stale-suppression / magic-number / duplicate-block sites
   were hand-edited that one engine command would have fixed. For
   duplicate-block the hand judgment (intentional parallel structure vs
   edit mistake) was still required before applying; for the other two
   kinds it was pure bypassed automation.
2. **Signature findings anchor at the def line with a 3-line marker window,
   ending at that line; one marker consumes one finding.** Stacked params +
   return findings need stacked markers INSIDE the window; wrapped/continuation
   lines push earlier markers out of the window and they self-report stale.
3. **`stale-suppression` is the 0.4.0 migration tool**: 80 markers from the
   0.2.0 sweep were stale (kinds renamed, exemptions added) — but re-audit
   each before deleting; a marker can look stale under `--file` and bind
   repo-wide.
4. **Cross-file duplicate/similarity suppressions self-report stale under
   `--file` mode** — `--repo` consumes them (false artifact).
5. **Display kind ≠ suppression kind**: similarity/large-class findings
   display as `standard`/`latent-class` but suppress with raw kinds
   `duplicate`/`data-clump`; using the display kind always self-reports stale.
6. **data-clump emits one finding per shared parameter pair** (deduped for
   display) — suppressing the visible one reveals the next pair; >3 same-anchor
   pairs can never fit per-site (only an ignore-file exemption covers it).
7. **undeclared-attribute does not resolve inherited declarations** — `Node`
   declares `display_name` (dag/node.py:178) yet subclasses get flagged;
   forced redundant re-declaration (tool bug, flagged upstream).
8. **`from __future__ import annotations` suspected of breaking return-type
   marker binding** — identical patterns bind without it (two independent
   observations, mechanism unconfirmed; workaround: NamedTuple conversion).
9. **ruff E501 (120-col) vs the 3-marker window conflict**: long whys cannot
   all fit — wrap continuation lines push markers out of the window. The
   working idiom: keep each marker one line ≤120, why truncated, citation
   token preserved; full verdicts live in this log.

### Where the tool cost us extra tokens (wrong-or-unclear verdicts)

- petrol.py `_attach_is_child`: marker "didn't bind" — cost a probe session
  (future-import hypothesis) before the one-marker-one-finding rule explained
  it; fix was a second stacked marker.
- api_cache `with_cache` return site: five probe rounds with differentiated
  comment texts before reading the scanner source for the window rule.
- rightmove_url `v >>= 5`: the flagged "32" was the second `0x20` on a
  different line; three marker placements before the report cleared.
- 458 fail-severity findings where 0.2.0 showed 0 — re-triage of ~580 sites
  is the inherent upgrade cost of a deeper scanner; the stale-suppression
  census (which markers it obsoleted) was genuinely useful.

### Dead code found during triage (deleted)

- `houses/web/geo_utils.py` — entire module, zero consumers (incl. dynamic/
  gitignored search) since before 0.2.0.
- `dag/http_error.is_transient_http_error` — dead duplicate of the houses one.
- `houses/web/broadcaster.push_rid` — only producer of a queue that never fires.
- `houses/context.py` — deleted whole: `get_scrape_fn` had zero callers and
  the `_request_scrape_fn` seam was write-only (tests set it, nothing reads
  it — their scrape fakes were no-ops). Removed the module, the
  `inject_server_deps` helper, and the `scrape_fn=` machinery at all 12 call
  sites in three test files (suite green after).
- `houses/nodes/transit._build_details` (zero callers) and
  `_route_description` (prod-orphaned; only test_old_behaviour pinned it) +
  its two test cases + the now-orphaned `_LEG_MODE_LABEL`.
- `houses/nodes/commute_breakdown_node._persons_source` — assigned, never read.

### Scope decision (user, 2026-08-31): wire formats are not an exemption

The 0.2.0 ruling ("serialized payload dicts suppressed as wire format by
design") is **overturned by the user**: "We can perfectly well have a class
with a to_dict method." Wire-format payloads become typed records with
`to_dict()` at the serialization boundary; raw dict literals are still
findings. ~420 existing wire-format suppressions across the repo cite the
old standard and are candidates for the same conversion — re-triage in a
future sweep, newest files first.

First application: `scripts/parse_netex_fares.py` — converted to
`NetexFareParser` (one class, the section parsers as methods — kills the
latent-class clumps), `StopCoord`/`NetworkFare`/`ParseResult` records with
`to_dict()`. Characterization suite (16 tests) written FIRST and kept green
throughout; all three `ignore-file` exemptions removed; gate clean with no
exemptions in the file. Suppressions remain only for keyed collections and
positional coordinate pairs (ceremony, not wire format).

**Boundary methods are not convertible records.** The ruling applies to
*data* records (fixed-field payloads built and passed around). It does not
apply to serialization-aggregator methods — e.g.
`PropertyNodes.to_json/to_json_summary/to_json_detail` (18 markers), which
`await` ~40 DAG nodes into frontend-pinned payloads: a record with 40
awaited async fields is ceremony. Those keep their boundary markers with
the standard citation.

### 0.4.0 suppression census

Recorded per-site in the working tree. Dispositions after the wire-format
conversion pass (2026-08-31):

- **Converted to typed records** (`to_dict` at the serialization boundary):
  parse_netex_fares, drive_isochrone payloads, tfl cache envelope,
  api_router what-if/staleness, intersection, admin_router, auth session
  claims, commute_router waypoints, station_shed, combined_map Leaflet
  configs.
- **Boundary/aggregator methods keep markers**: PropertyNodes.to_json*,
  api_router response aggregators, payload_checks, DagJSONEncoder paths —
  the wire shape is owned by the method, not by a data record.
- **Keyed collections / external contracts keep markers**: cache-key
  identity threading, ORS/Places/Overpass request params, Google OAuth
  claims, BODS dataset payloads.
The 0.2.0-era census above is historical.

`make lucidlint`: **GATE PASS — 0 fail-severity actions, 1 accepted census
warning.** `make test`: **1571 passed.** ruff + pyrefly: clean. lucidlint
0.4.0 (`f8cbbc8`) pinned in pyproject.toml.

## Tool behaviours worth knowing (2026-10-06)

Two things cost real time while landing the `node_results` split, both verified
against the tool:

- **A suppression binds within the 3 lines ending at the finding.** A marker
  one line above a `def` covers the *function* findings, but a finding on a
  signature or on a literal INSIDE the body needs its own marker within three
  lines above it — otherwise the marker is itself reported as a stale
  suppression ("markers bind within the 3 lines ending at it").
- **The baseline keys findings by file:line, so moving lines re-surfaces
  otherwise-untouched findings.** Editing `current_home_node.py` shifted
  `to_dict`'s line and its long-accepted record-shape finding reappeared as an
  action. When a sweep reports findings in code you did not write, check
  whether you moved it before fixing it.

## The baseline is gone (2026-10-06)

`lucidlint.json` was a list of 95 acknowledged actions. It is deleted, the make
target no longer passes `--baseline`, and the gate reports zero failing
actions. What made that possible without touching what we store:

### `dag.persistence.WireRecord` — the record the wire already had

Every `record-shape` finding was a dict with constant keys: a record whose
shape only existed in the literal that built it. A plain class is not a fix —
`json.dumps` raises `TypeError: Object of type MonthlySide is not JSON
serializable` through the DAG's own encoder, so storing one would have changed
what reaches the database and the frontend.

`WireRecord` is a frozen dataclass that ALSO implements `Mapping`, so
`value["key"]`, `.get()`, iteration and `len()` keep working for every existing
reader (found the hard way: `TypeError: 'BaselineWire' object is not
subscriptable` in `tests/unit/test_monthly_delta.py` when records were not
Mapping). `DagJSONEncoder` projects it with `dataclasses.asdict`, so the stored
JSON is byte-identical to the literal's — verified for nested records.

### Parsed JSON takes an annotation, not a class

A `from_dict(raw: dict)` parameter is parsed data, so the fix is the narrowest
honest annotation: `Mapping[str, Any]`, or a precise `dict[str, str]` when the
value is a map (node id → timestamp) rather than a record. Both clear the
finding and accept every existing caller. A `TypedDict` works only where the
caller can supply one (an opaque `json()` result); where a value typed
`dict[str, Any]` flows in, pyrefly rejects the TypedDict parameter
(`bad-argument-type`) and rejects a TypedDict flowing into a `dict`
(`bad-assignment`) — measured, not assumed.

### Tool behaviours that cost time here

- **`lucidlint fix` writes the file even without `--confirm`.** Run it only
  when you intend the change.
- **Its `extract-record-class` seam was unsafe for wire payloads**: it inserted
  the class above the module docstring (demoting the docstring to a bare
  expression) and made `to_provenance_value` return the object rather than the
  dict the provenance tree stores. Preview, judge, and reject when the seam is
  wrong — do not hand a `to_wire`/`to_provenance_value` boundary to it.
- **A scratch file in the repo root is linted as a repo file** — one probe
  script became the top failing action during the sweep. Probes belong in
  `/tmp`.

### Corrections from doing it at scale (14 files, ~90 sites)

The rules above held, but three refinements came out of the sweep and two traps
came with them:

- **Collections need a named alias.** `list[dict]` and `list[Mapping[str, Any]]`
  are both flagged; a `type X = dict[str, Any]` alias (PEP 695) clears it with
  no casts and no caller changes. The same alias avoids the follow-on
  `latent-class`/"strewing" finding that several free functions sharing a
  `TypedDict` row type triggers.
- **Return types are stricter than parameters.** A map-shaped return
  (`dict[str, str]`, `dict[str, float]`) is exempt, but `-> dict[str, Any]` and
  `-> dict[str, object]` are not. For a wire record's `to_dict` the clean route
  is to drop the method and project at the writer (`dict(record)`), which is
  byte-identical under `json.dumps`.
- **A `TypedDict` constructed with keywords** (`Wire(couple=..., others=...)`)
  clears a constant-key literal without being a class at all — a plain dict at
  runtime. Useful where a `WireRecord` would be wrong.
- **TRAP: `WireRecord` is a `Mapping`, not a `dict`.** Any consumer doing
  `isinstance(value, dict)`, or any function annotated `-> dict`, rejects it —
  it broke `dag.attempt.project_value` for the provenance shape, which is why
  the provenance side uses annotations rather than records. Reach for
  `WireRecord` only where the value is *served*, and prefer the keyword-TypedDict
  or the alias when a `dict` type is expected downstream.
- **TRAP: `Mapping` in a parametrised position breaks invariance.** A caller
  passing `list[dict]` to a `list[Mapping[str, Any]]` parameter is a pyrefly
  error, so widen signatures only where the caller's type actually matches.


## 0.6.7 sweep (2026-10-08) — the census era

Repin `1019627b` (0.6.1) → `5c893cbc` (0.6.7). The 0.6.7 rule set changed in
ways that invalidate several earlier verdicts, and the tool gained a
**suppression census**: a `bulk-suppression` WARNING fires per signal suppressed
at ≥10 sites repo-wide (attached to the file with the most). The user's scope
for this sweep: clean gate, **no baseline, no config ignores, suppressions near
zero** — so the census gate is binding, not advisory.

### What 0.6.7 changed under us

- **global-state now fires on ANY module-level assignment — constants
  included.** `SECONDS_PER_MINUTE = 60` and the named data tables the
  magic-number doctrine had us create are all findings. Disposition (user
  ruling): quantities move to `timedelta`/Pint/`Money`; tunables and data
  tables become **class attributes** of the class that serves them; scripts
  with no class get a small program/config class (probe-verified clean — no
  static-husk/assembly-class on a constants-only class).
- **Money doctrine (user ruling)**: money is only the `Money` class; no
  `PENCE_PER_POUND`-style factors in business code — pence→pounds lives in ONE
  money-layer function per file (`_pence_to_money`), which carries the single
  `/ 100` with a written why. Return-trip costs are `Money * 2`.
- **`[tool.lucidlint]` config ignores are gone** (user: no baseline). Every
  hidden finding is now fixed or carries a per-site why.

### Marker-binding mechanics — probed this sweep (do not re-derive)

1. A marker binds a finding when it sits on the finding's line, the line
   before, the logical line's first source line, or the line before that —
   **from the marker's side it is the finding's window, not a free 3-line
   radius**.
2. **Decorated defs/classes**: the marker must sit BETWEEN the decorator and
   the `def`/`class` line. Above the decorator does not bind.
3. **Chained-comparison literals** (`51.5 < x < 51.52`) cannot be suppressed by
   a marker at all (above or same-line tail, both probed). Deterministic fix:
   name the literals as class attributes — the constant-definition RHS is
   exemption territory.
4. **`duplicate-block` binds above the SECOND twin** in repo mode; per-file
   mode anchors the first. Extracting the shared part is the only placement-
   independent fix.
5. **2-line markers**: only the line carrying `lucidlint:` is a marker; the
   continuation is dead text. Every 2-line marker in the tree was unbound —
   each was re-anchored single-line.
6. **Cross-file families self-report stale under `--file`** (duplicate,
   duplicate-module, record-shape similarity): the marker binds in `--repo`
   mode only. The per-file stale-suppression report is a false artifact.
7. **`record-shape` — three scanner-clean patterns** (probe-verified): a
   PEP 695 `type X = dict[k, v]` alias for keyed collections; `Mapping[str, Any]`
   for parsed JSON; a named `TypedDict` built with keyword construction at the
   writer, with the function's return typed as it. `dataclasses.asdict` alone
   at an untyped return is NOT clean.
8. `dataclasses.replace(record, **{dynamic_field: v})` is untypable
   (pyrefly `bad-argument-type`): wrap in `cast(...)` and, where pyrefly still
   reports, `# type: ignore[bad-argument-type]` with a why.

### Structural conversions this sweep

- **Seam state classes** (probe-verified pattern: class attributes are clean,
  including mutation from functions): `dag.persistence.Runtime` (`path`,
  `testing` — the DB seam, read by settings/scheduler/tests),
  `_Schema`/`_SchemaCache`, `houses.database.Runtime`, `_BroadcastState`
  (queue/clients/debounce), `_OAuthStateStore`, `_ServerRuntime.loop`,
  `_ChromeRuntime` (scraper subprocess), `_ScrapeConstants`, `_ApiPolicy`,
  `_MapColors`, `_GeocodeApi`, `_EpcEndpoints`, `_TaxRates` (council-tax band
  ratios + cache), `DriveIsochroneConfig`/`StationShedConfig`/`IntersectionConfig`
  (tools), `SettingsNode` (node IDs + `SETTING_DEFAULTS`/`API_KEY_TO_NODE`).
- **Pint/`timedelta` unit fixes**: `capture_dom.LOGIN_TIMEOUT = timedelta(minutes=10)`,
  route durations via `Quantity(seconds, "second").to("minute")`,
  `dag.derived_node` retry caps as timedeltas, `scrape_queue` backoff policy as
  timedeltas, `Person.bus_walk_penalty = Quantity(30, "minute")` (the aliased
  `from pint import Quantity as _Quantity` hid the unit-constructor exemption —
  the real name is required).
- **`dag/guard.py`** — shared never-raise guards (`guarded`/`guarded_async`)
  so the DAG-subclass and user-callable boundaries carry ONE suppression
  instead of one per call site (the census counts sites).
- **static-husk**: `_WhatIfRoutes`/`_PropertyPatches` became module endpoint
  functions with `@api_router.<verb>` decorators (the file's own majority
  pattern); `services.py`'s `_Default*` classes took their real state.
- **class-module**: the 0.4.0 precedent held — split/rename to a file named
  after the class (13 new `houses/apis/*` DTO modules); the small wire-DTO
  modules then trip `duplicate-module` (parallel TypedDict boilerplate) and
  carry a module-head marker with that why.
- **Unused**: dead classes deleted (`BusJourney`, `CommuteMode`,
  `NearestStationNode`, `SinkingFundRate` id, `TOTAL_SHARE_PERCENT`); test-only
  seams carry `unused` markers stating they are test-exercised.

### Engine/verify lessons

- `lucidlint fix` remains per-kind and silent; every applied fix was
  diff-checked. The `extract-record-class`/`split-module` seams were not used —
  hand conversions with the probes above were deterministic.
- Wave-based subagent conversion works, but **agent reports are optimistic**:
  two wave-1 regressions (a dropped `selling_home=True` in
  `make_default_persons`, three dropped `API_KEY_TO_NODE` entries) and one
  missing `TypedDict` import passed the agents' own per-file gates and were
  caught only by the full suite. The full suite is the integration gate; run it
  after every wave.
- Concurrent agents editing one file produced transient broken states
  (`dag/http_error` docstring, `tile.py` constant) — slices must be
  file-disjoint, and cross-slice reference flips need an explicit owner.

### The suppression census — final state (2026-10-08)

The 0.6.7 census gate (a `bulk-suppression` warning per signal suppressed at
≥10 sites) was driven from ~650 suppressed sites to, per signal: broad-except
5, class-module 8, latent-class 9, duplicate-block 2, duplicate-module 4,
unused 9, middle-man 8, magic-number 5, record-shape 5 — **every signal under
the gate, zero reported actions.**

### Wire formats get classes, not markers (owner ruling, 2026-10-08)

The 213 record-shape markers left after the first pass were the "boundary
verdict" family (the 2026-08-31 "boundary methods are not convertible records"
ruling). The owner overruled the marker route: **"Being a wire format is no
excuse not to have a class with a to_dict or to_json method."** Every one was
converted to code with three patterns:

1. **A record's `to_dict` returning a dict literal** → declare
   `<Record>Wire(TypedDict)` beside the record, type the return, build with
   keyword construction. Conditional/optional keys: `NotRequired[...]` + item
   assignment after construction. Nested records: `list[_InnerWire]` fields.
   The record class and its `to_dict`/`to_json` stay — the wire shape gets a
   name and the compiler checks the construction.
2. **A raw wire dict at a writer** → the same named-TypedDict construction,
   with the writer's return typed.
3. **Keyed collections / lookup tables** → a PEP 695 `type X = dict[k, v]`
   alias, or a pairs-comprehension (a 2+-constant-key literal with variable
   values fires even with an alias — probe-verified).
4. **Parsed-JSON parameters** → `Mapping[str, Any]` (`object` ripples into
   every read and breaks pyrefly at the consumers).
5. **FastAPI bodies** → `Mapping[str, Any]`; where the latent-class pair rule
   then fires on shared parameter names, each handler gets its own payload
   noun (`address_patch`, `location_patch`, …) — distinct names state distinct
   payloads.
6. **Cross-cycle seams** → the cycle was removed by naming each side's wire
   blocks (`tfl_client._TflLegWire`/`_TflJourneyWire` vs
   `transit_route._DrivingLegWire`) instead of reproducing a payload through a
   shared dict.
7. **WirePayload seams** (`api_cache.with_cache`, `apis.epc.search`,
   `GoogleRoutesClient.post`) → records inherit `WirePayload` and override
   `to_dict` with `@override`; under pyrefly's default preset a TypedDict
   return is not assignable to the protocol's `-> dict`, so nominal
   inheritance is the clean route.

Conversion notes worth keeping: the `houses/apis/*_params.py` splits from the
class-module work hid a real bug — `OrsReverseParams.wire_key_rewrites` missed
`boundary_country` → `boundary.country`, so the reverse-geocode request sent a
key Pelias does not read; fixed during the conversion. The pyrefly lock was
regenerated (47 entries) for the deliberate signature changes — the residue is
test literals passed where the APIs now want the named wires; updating those
literals is the next tidy-up, not a correctness issue.

### Correction (2026-10-08, owner ruling): the payload twins were wrong

The "wire formats get classes" pass above over-read the record-shape finding.
The rule is: **the named type is the class that carries the serialisation
method; `to_dict`/`to_json` return a plain dict; the returned object is not
itself typed with a second class.** The named TypedDict twins (`*Wire` beside
every record) were therefore reverted — `to_dict` returns `dict` again, and
the twin declarations are deleted repo-wide (75 classes).

The finding that drove the twins is a tool bug, reported as
**ashbywinch/lucidlint#42**: `record-shape` fires on `def to_dict(self) -> dict`
(the return arm keys on the annotation, `scanner/src/checks.rs:7638`) and again
on the constant-key literal inside the method, although the enclosing class
names the shape. The tool already carries the mirror exemption on the
ingestion side (`checks.rs:7574`: a `from_dict` function in a module with a
`from_dict` class produces no finding); the export side needs the same. Until
the fix lands the gate reports those findings and they are deliberately left
visible — no marker, because the code is correct and an exemption comment
would be exactly the excuse pattern this log exists to prevent.


### Serialisation migration (owner ruling, 2026-10-17): one type per thing

Owner ruling: a wire format is no excuse for a parallel class. The named type
is the class that carries the value, and it serialises itself. What follows from
it, and why findings in this family are *not* fixed:

- **HTTP responses** are Pydantic models + `response_model=`; the response model
  *is* the API's contract type, not a serialisation twin, so it survives the
  rule.
- **`to_json()`** exists only where our code owns the bytes (websocket messages,
  artifacts, DAG rows) — never where a consumer (FastAPI, httpx) will encode
  again.
- **Third-party types** (`money.Money`, `pint.Quantity`) get a serialiser on the
  field that carries them (`MoneyValue` / `QuantityValue` in
  `serialization/__init__.py`), never a class beside them.
- **Mirror classes are deleted, not renamed.** The `*Json`/`*Wire` suffix is
  gone repo-wide (82 classes), and every class whose only job was to hold a wire
  shape was deleted rather than converted: `MoneyJson`, `NodeJson`,
  `_WalkToTown`, `_StopFare`, `_PetrolAugment` — plus the pattern that keeps
  recurring, **a record whose one method unwraps the dict it holds**
  (`api_cache._UrlQuery`, `commute._CommuteValue`).
- `SerializableAsDict` (`serialization/__init__.py`) is the one base for boundary
  records, both directions. How to write one is in
  [coding-standards.md](coding-standards.md) → *Boundary records*, the single
  source for aliases, the two field conditions, and when a wrap serializer is
  warranted.

Probes worth not re-deriving:

- pydantic cannot build a schema for `money.Money` at all without
  `arbitrary_types_allowed`; `response_model=` with an
  `Annotated[Money, PlainSerializer(...)]` field emits byte-identical JSON,
  aliases included — but FastAPI re-dumps a returned model before validating it
  against `response_model=`, so the field type must be able to read its own
  output (the `BeforeValidator` half of `MoneyValue`).
- `exclude_none` is not a substitute for a per-field condition: it recurses and
  strips Nones from nested node maps that belong on the wire.
- `wire_key_rewrites` was deleted with the alias move, and the reason is itself
  the finding: a rename map keyed by field name fails **silently** — the sweep
  found one missing `boundary_country → boundary.country`, i.e. a country filter
  absent from every request, with nothing raised anywhere.
- A per-field `exclude_if` condition is not a wrap serializer, and the
  difference matters for the gate: the wrap is a method returning a dict, so it
  is a `record-shape` action wherever a field rule would have done.

Byte identity was the acceptance test, not reading the diff: a throwaway oracle
captured the pre-migration bytes of the 10 endpoints, the three `PropertyNodes`
projections, the websocket envelopes and the DAG rows, and compared them after
each step with run-to-run volatile values masked (timestamps, object counts, tie
order); committed artifacts were round-tripped the same way
(`data/commute/station_shed.json`, 400,969 bytes, byte-identical). The full
suite stayed green throughout.

#### Tool bugs filed (open)

- **lucidlint#42** — `record-shape` fires on a method or function annotated
  `-> dict` that returns a record's dict, and on the constant-key literal inside
  a serializer. The shape *is* named at the build site, so these are left
  visible with no marker: a marker here would be the excuse pattern this log
  exists to prevent.
- **lucidlint#44** — `middle-man` fires on delegation that *is* the
  encapsulation (a library base, a Protocol implementation, a call that fixes an
  argument): https://github.com/ashbywinch/lucidlint/issues/44, filed with a
  verified repro.
- **lucidlint#46** — `record-shape` fires on a typed map of records
  (`dict[str, Provenance]`) passed to a function, even one that only counts it;
  the value type is the noun and the operations do not belong to the collection.
  Filed with a repro. Two sites: `dag/attempt.py:436,618` (method parameters).
- **lucidlint#47** — `god-class` is a **failing** action even when the partition
  rule finds no seam (`seam_members: []`), while its own message says size alone
  is a review signal; the only way to pass is to suppress it, and ten
  suppressions then trip the census. Seven markers sit on it. Filed with the
  probe that shows `seam_members: []`.
- **lucidlint#52** — `class-module`'s multi-class arm reports a module that holds
  its namesake class plus that class's own DI interface (a `Protocol` satisfied
  by the namesake class and by test doubles), although the single-class arm's
  message carries the "closely related models" exception the standard states.
  https://github.com/ashbywinch/lucidlint/issues/52 — filed 2026-10-21 with a
  ten-line repro. **No marker sits on the site**: the file is silent today only
  because of the *second* quirk reported in the same issue — `class_module_findings`
  exempts any module that contains a module-level `def` (`has_module_fns`), and
  `google_routes_client.py` now has parse helpers. Verified A/B: a module with a
  misplaced class fails, and the same module plus one helper passes.


**`duplicate-module` (4 suppressions)** — the request DTOs in `houses/apis/` are
structurally identical *by design* (one class, one docstring, N fields), which
is what the doctrine produces; "reconcile the copies into one" is not
actionable on different request shapes, so each site carries a marker whose why
says so.

**The DAG core is deliberate, not pending.** `dag/node.py`,
`dag/derived_node.py` and `dag/attempt.py` carry 15 of the 38. They are two
families and neither is a mechanical conversion:

1. the `-> dict` arm of #42 (`to_json`, `to_json_value`, `_safe_result_dict`,
   `_build_provenance_dict`, `_enrich_json`) — the shape *is* named at the build
   site;
2. records on `dag/persistence.WireRecord`, which are dict-like **on purpose**:
   its docstring records the decision — a node value is read as `value["rid"]`
   all over the codebase, so attribute-only access would be a second, needless
   migration. Converting them to `SerializableAsDict` was attempted and reverted
   the same hour: `tests/unit/dag/test_architecture.py` forbids `dag/` importing
   `houses/`, so the base would first have to move into the DAG layer, and the
   subscript readers would have to move with it.

If the DAG's node values are ever to become models, that is a slice of its own
with its own decision: move the serialization seam into `dag/` (it is the lower
layer, and the seam is used by both), then migrate the readers.

Current state: **35 open `record-shape`** — the DAG core above (deliberate), the
serializers that return a dict by definition, and the remaining per-file records
(`model/domain` 3, `api_cache` 2, `transit_route` 2, `rightmove_scraper` 1,
`services` 1, `monthly_delta` 1).


### The ten class-signal suppressions, probed (2026-10-21)

Ten `# lucidlint: ignore …` comments sit on class-size signals, and the census
warns because of them. Each marker was probed this time — the marker removed in
a copy, the tool re-run, `seam_members` read — rather than trusted:

| Site | Signal the tool reports | `seam_members` | Verdict |
|---|---|---|---|
| `commute_router`, `tfl_client`, `property_nodes`, `bus_journey`, `derived_node`, `attempt`, `parse_netex_fares` | `god-class` | `[]` | **nothing to fix** — no field-disjoint group, and the tool's own message says size alone is not a split order. Filed as **lucidlint#47** |
| `api_cache._make_key` and its four siblings | `data-clump` | `['body', 'method', 'params', 'url']` | **real seam** — four parameters threaded through five functions are one thing: the request's identity. Fix: a `CacheRequest` record, and the five seams take it |
| `sync_parking_rates.main` | `assembly-class` | `[]` | **real seam** — `main` threads `args` through four functions; a class in waiting |
| `rightmove_scraper.to_property` | `feature-envy` | `[]` | **nothing to fix** — the advice is to move the computation onto the record, but it reads the page (DOM + preloaded state), so it cannot live there |

Two facts worth keeping:

- The census's "`latent-class` suppressed at 10 sites" is an artefact of our own
  comment text: the ten markers sit on **four different signals**
  (`god-class`, `data-clump`, `feature-envy`, `assembly-class`) because a
  marker's kind name need not match the finding's signal. The count is not a
  count of one rule.
- The `api_cache` marker's why records a *reason not to fix* — the earlier probe
  declined because a `CacheRequest` "re-fires first-arg". That is the pattern to
  stop: a marker is the last resort after a probe, never a place to park the
  next finding.


### The serialization seam moved to its own library (owner ruling, 2026-10-17)

The DAG conversion could not start: `tests/unit/dag/test_architecture.py`
forbids `dag/` importing `houses/`, and the seam lived in
`houses/web/json_utils.py`. Owner ruling: it is not coupled to the DAG, so it
belongs in a library of its own that every layer may use — **`serialization/`**
(`SerializableAsDict`, `MoneyValue`, `QuantityValue`, `omit_when_none`,
`omit_when_empty`, `asdict_serializable`, `optional_parse`).

`houses/web/json_utils.py` is deleted; 35 importers now read
`from serialization import …`. The layering test gained the
`serialization` layer and lets `dag` depend on it (and nothing else).

Two forks the move surfaced, both consolidated rather than marked:

- `dag/attempt.py`'s three HTTP status constants were module-level (the
  `global-state` finding); they are now `RetryableStatus` on the class the
  decision serves, which also made the class public.
- `houses/helpers.py` had a byte-identical `_HttpStatus` (the same three
  constants) — a real fork the `duplicate-module` rule caught the moment the
  DAG's copy was named. `_HttpStatus` is deleted; the helper imports
  `RetryableStatus`.

Two warnings remain, both deliberate: the **census** fires because
`record-shape` is suppressed at 12 sites, and its advice (move the rule to
`[tool.lucidlint]` guidance) contradicts the standing owner scope decision
*no blanket config ignores — per-site treatment only*, so the per-site markers
stand; and the **middle-man** warning on the base's `to_dict` is left visible
on purpose (lucidlint#44).


### The serialization-plan pass (2026-10-21): the pin moves, five items land

The plan (`serialization-migration-plan.md`) was implemented: order items 1-4
and item 6's third arm in code, plus the pin bump item 6 was waiting on. Gate:
**13 actions = 12 `record-shape` + 1 `middle-man` warning** (from 26 = 24 + 2),
1842 tests, typecheck clean, and the byte oracle identical on all 7 captures.

**The pin: `5c893cbc` → `2bc734f`** (PR #43, the #42 export exemption).
Measured A/B on one clean worktree at `813c43e6`, both tools, same tree:

| | old pin | master |
|---|---|---|
| findings | 26 (24 fail + 2 warn) | 17 (15 fail + 2 warn) |
| comment-suppressed | 175 | 175 |

Nine `record-shape` sites clear, no new finding appears, and **no marker goes
stale** — the exemption only removes findings, so the risk was a marker left
suppressing nothing; the unchanged census proves none was. What clears is
exactly the export-edge arm: `to_dict`/`to_json*`-named methods whose enclosing
class declares fields covering the literal's keys (`dag/node.py:267,287`,
`dag/derived_node.py:1044,1050`, `houses/model/domain.py:96,97`,
`houses/nodes/commute.py:377,406`, `houses/services.py:111`). **The fix is
narrower than #42's report**: a `-> dict` helper that is not an export name, a
module-level serializer target (`_money_wire`, `_quantity_wire`), a wrap
serializer's `-> dict`, and the parameter arm all still fire. #42 stays open
upstream for those; the 12 residue sites carry no marker on purpose.

**Items 1-4, 6's third arm** (per-file evidence in the plan): the cache seam is
a `CacheRequest` class (five methods, the four threaded parameters as its
state; keys byte-identical — 13 shapes checked against the old `_make_key`, and
the committed fixture cache still resolves); `ParkingRateSync` owns the parking
sync's run; the group block is rebuilt as `dag.node.NodeValue` and written back;
`_GoogleClientConfig` is a boundary record with one wrap serializer and its
TypedDict twin is deleted; `_with_coordinates` names the coordinate pair once.

Probes worth not re-deriving:

- **A tuple return annotation still trips `record-shape`.** The first attempt at
  the group block returned `tuple[NodeValue, MutableMapping[str, Any]] | None`
  — the tool still reports the method, with `MutableMapping[str, Any]`,
  `dict[str, Any]`, even `object` in the tuple, and with the body reduced to
  `return None` (annotation-driven, not body-driven). The shape that clears:
  return the SLOT (the mapping that owns the key) from one helper, and read the
  wire back out of that same slot key.
- **`NodeValue.to_dict()` alone is not byte-identical to a node's served wire.**
  A settled derived node's wire is the dump PLUS the lifecycle keys
  `DerivedNode._enrich_json` appends (`retry_at`/`retry_count`/`stale`);
  `model_validate` ignores extras, so replacing the wire outright drops them.
  Lay the rebuilt wire OVER the block's own (`{**wire, **record.to_dict()}`) —
  the key set and order stay the block's, the values become the record's.
- **The census warning is gone as a side effect.** It fires per signal
  suppressed at >=10 sites; after this pass the counts are `latent-class` 8 and
  `record-shape` 7, so the warning the 0.6.7 section called deliberate is no
  longer reported. The `middle-man` warning on the base's `to_dict` remains
  (lucidlint#44).

Still blocked, unchanged: **#46** (the two `dag/attempt.py` sites) and **#47**
(the seven class-size markers; the seventh is load-bearing for a failing
`process-class` on `NetexFareParser`, which needs the parse data modelled as
classes first).


### The unblock pass (2026-10-21, same day): 12 fails → 4

The plan's blocked residue was worked rather than parked, and the residue turned
out to be code-shaped after all — **12 fails → 4**, every one of the 8 in the
repo's own shapes:

| Site | Shape |
|---|---|
| `dag/attempt.py:436,618` (#46) | `ProvenanceSources(dict[str, Provenance])` — the named keyed collection, with the stored-block parse (`from_dict`) on it |
| `dag/derived_node.py:649,811,1033` | `NodeValue` models the lifecycle keys; `to_node_value`/`_wire_value` are the builders; `_safe_result`/`_build_provenance` return records and `_persist` dumps at the persistence edge |
| `houses/nodes/petrol.py:201`, `houses/nodes/commute.py` | each node's own row is a `NodeValue` subclass (`_SchoolCommuteRow`, `_CommuteSelectorRow`) named by `wire_type`; `_enrich_json` sets the record's fields |
| `houses/commute_router.py:74` | the wrap serializer's return is `Mapping[str, Any]` (the repo's own convention for wrap serializers) |
| `houses/model/domain.py:143` | `Person.to_provenance_value() -> _PersonProvenance`, a named `TypedDict` wire (the `MeasurementProvenanceValue` pattern) — `to_dict` deleted |

Two lessons worth more than the finding count:

- **A new builder seam bypasses every subclass override of the old one.** Moving
  the persist path from `to_json()` to `to_node_value()` silently dropped
  `CommuteSelectorNode`'s value projection: the persisted commute row stored
  `_details` where the wire states `details`. The **byte oracle caught it in one
  run** (`dag_rows.json` → `.test_shape/1/Bracknell/commute.value`) and nothing
  else did — 1843 tests were green. The fix is the seam the override belonged
  on: `Node._wire_value(attempt)`, which both paths now go through. Any future
  builder seam must grep for overrides of the method it replaces.
- **The class-size findings are warnings, not fails.** Deleting
  `houses/commute_router.py`'s marker and re-running the gate reports
  `[warn][latent-class]` and leaves the fail count unchanged — so the six
  markers #47 covers are not load-bearing for the gate at all, and the plan's
  premise that they move the failing count was wrong. They stay (they record a
  probed verdict) until #47 removes the finding itself.

**The 4 that remained** were first recorded here as "the tool's, not the code's"
— that was **wrong**. They were fixed the same day by naming the shapes the rule
asks for, without touching the tool: `MoneyWire`/`QuantityWire` TypedDicts built
with keyword construction (the pattern this log already lists as scanner-clean)
and `type RecordWire = dict[str, Any]` for the boundary-record base — named, and
still mutable, because the web layer patches a payload at its projection
boundary. Gate: **0 fails**. The lesson: "the doctrine forbids the rule's
suggestion" is not the same as "there is no honest shape"; check the
annotation-level answer before calling a finding unfixable.


### The exemption audit (2026-10-21): every remaining site, one at a time

The tool was **not** edited. The last four failures were fixed in code by naming
the shapes the rule asks for (`MoneyWire`/`QuantityWire` TypedDicts built with
keyword construction; `type RecordWire = dict[str, Any]` for the boundary-record
base — named, and still mutable, because the web layer patches a payload at its
projection boundary). Gate: **0 fails**.

Every remaining suppression was then re-read against its code. Eight were
**wrong** — they kept dead code alive — and are deleted with their tests:

| Deleted | Why it was not a seam |
|---|---|
| `dag/persistence.py::_serialize_value`/`_deserialize_value` | the `_type`/`_module` marker codec is unreachable: `record_columns` + `DagJSONEncoder` write plain JSON. Their two test files (`dag/test_persistence.py`, `unit/test_persistence.py`) asserted the dead encoding; the live round-trips in the latter are kept, re-stated on the stored dict |
| `houses/helpers.py::is_transient_error` (whole module) | a fork of the retry policy whose only caller was a test; the test now calls `DerivedNode._is_transient_error` → `classify_exception`, the single source of truth |
| `houses/nodes/commute.py::format_duration` | "the web layer no longer formats" — and nothing else called it; the frontend formats |
| `houses/nodes/expressions.py::StampDutyFn` | superseded by `StampDutyNode`'s `TieredRate` table (which carries provenance) |
| `houses/stamp_duty.py::stamp_duty_land_tax` | a second copy of the statutory bands, reachable only from `StampDutyFn` — a drift risk, deleted with it |
| `houses/nodes/transit.py::CommuteResult` + `CommuteLeg` | superseded by `Commute`; the one test using it was a tautology (it built the record and asserted a field) |
| `houses/nodes/transit.py::WalkLegCheckNode` | superseded by `BusRouteNode._walk_too_long`; the pipeline tests' walk-check double is now a `UserInputNode[bool]` |

One citation was **uncheckable** and is now written down: the `fakefs` markers
cited `docs/testing-standards.md`, which said nothing about the filesystem. The
policy (real `tmp_path`, no pyfakefs, and why) is now a section there.

What follows is the rest, by signal. A verdict is "stands" only where the reason
is checkable from the code or a written decision; **weak** marks a reason that
does not survive the re-read.

#### fakefs — 89 sites

- **4 file-level markers** (`tests/unit/test_deploy_run_migrations.py:22`,
  `test_deploy_migrations_list.py:23`, `test_deploy_allowlist.py:15`,
  `nodes/test_works_equity_mortgage.py:3`): subprocess interop against a real
  sqlite file / a stub `sudo` on PATH. The rule's own message carves this out
  ("when the code under test needs real FS semantics — subprocess interop,
  symlinks, C-level I/O like sqlite3"). **Stands.**
- **5 sites whose test body touches no filesystem** —
  `nodes/test_commute_pipeline_rebuild.py:112` (pure in-memory pipeline rebuild),
  `test_parse_netex_fares.py:107,132,149,202` (in-memory XML strings),
  `test_property_registry.py:34` (an in-memory registry). Read line by line: no
  `tmp_path`, no `open`, no `Path`. The detector is keying on the module or a
  fixture, so the finding is a false positive at these sites. **Stands** (and is
  worth a tool bug if it recurs).
- **80 sites**: real-FS tests isolated by `tmp_path`, writing CSV/JSON/HTML with
  `open`/`write_text`/`csv.writer`, plus `test_docs_links.py:32` (reads the
  repo's own docs on purpose) and the e2e Playwright ones. pyfakefs would work
  for the plain file I/O — the reason not to is the house decision, now written
  in `docs/testing-standards.md` → *Real filesystem, not pyfakefs* (sqlite3 is
  C-level I/O, Playwright and subprocesses need a real FS, and the isolation
  that matters is `tmp_path` + the cache/DB fixtures). **Stands**, now
  checkable.

#### class-module — 8 sites — **resolved, plus two tool gaps**

**Resolved (2026-10-21)**. Three groups:

1. `signals.py` → `signal.py` / `slot.py` / `connection.py` (the handle now holds
   its teardown, so the imports are acyclic).
2. `commute_router.py` → the Google Routes client, its interface
   (`GoogleRoutesClientLike`) and its private wire records in
   `google_routes_client.py`, the options record in its own module, the three
   projections the client returns one class per module (`route.py`,
   `transit_stop.py`, `bus_route.py`), and the wire knowledge moved out of
   `CommuteRouter` and `BusRouteNode` entirely.
3. The six family files → 22 modules, one public class per module, named after it
   (`walkability_node`, `nearest_town_node`, `town_desc_node`, `town_node`;
   `bus_route_node`, `bods_fare_node`, `bus_leg_augment_node`; `pmt`, `tax_tier`,
   `tiered_rate`; `best_address_node`, `postcode_node`, `best_location_node`;
   `school_acceptance_node`, `nearest_school_node`, `school_location_node`;
   `property_view`, `property_summary`, `property_detail`, `school_entry`,
   `schools_block`, `triage_block`). All eight markers deleted.

**The lesson the splits paid for:** one module per *wire shape* is not the same
as one module per class. The first cut created eleven single-TypedDict modules
and the gate answered with six `duplicate-module` fails — sibling small
TypedDicts of near-equal size are structurally identical to the rule. So a
private wire block that only one document emits now lives *privately in that
document* (the standard's "closely related small dataclasses" case), and only
the shapes both documents carry (the school entry, the schools block, the triage
block) are public modules. Making that honest moved the document assembly out of
`PropertyNodes` into `property_summary(nodes)` / `property_detail(nodes, svc)`,
and made the four commute-projection helpers public on `PropertyNodes` (the
recorded "private-import — renamed public" precedent). Wire bytes unchanged.

**Owner ruling on what the class-module exemption may cover** (2026-10-21):
suppress/mark only (a) **private** classes — which the rule already skips — and
(b) **interfaces whose only implementations are the namesake class and test
doubles**. Public dataclasses do NOT qualify: they get their own module.

**The tool gaps the split exposed — filed 2026-10-21 as
[lucidlint#52](https://github.com/ashbywinch/lucidlint/issues/52)**: (1) the
multi-class arm reports the module's own DI interface (a `Protocol` satisfied by
the namesake class and test doubles) as a misplaced class; (2) `class_module_findings`
exempts **any** module containing a module-level `def` (`has_module_fns`), so a
file's verdict flips when an unrelated helper moves in or out — verified A/B and
reported in the same issue. No marker is needed today: `google_routes_client.py`
is silent via quirk (2), which is exactly why the quirk is worth fixing before
trusting that silence.

The remaining six sites are still family files (area, bus, location, schools,
expressions, property_nodes) whose verdicts stand as recorded: splitting each
family is a module-per-class change with no behavioural gain. **Owner call.**

#### inline-import — 5 sites — **resolved (2026-10-21)**

All five are gone, by the leaf-module route the 2026-08-22 decision prescribes:

- **The DAG triangle** (`dag/persistence.py:538`, `dag/scheduler.py:224,400`):
  the processor-thread state moved to a leaf, `dag/processor.py`
  (`ProcessorRuntime`, `current_processor_thread`, `assert_mutation_allowed`), so
  persistence imports the guard from the leaf and scheduler imports persistence
  and the leaf at module top; scheduler's `isinstance(dep, DerivedNode)` in the
  re-queue path was redundant (`self._scheduled` only holds queued nodes), so the
  `derived_node` import went with it. No re-exports; every importer repointed.
- **`houses/services.py:429`**: `services_provider` has been a leaf since the
  0.6.7 fix, so the top-level import is safe (aliased to avoid shadowing the
  method name).
- **`scripts/sync_parking_rates.py:332`**: the three pure helpers
  (`extract_daily_rate_from_tariff`, `make_slug`, `city_slugs`) moved to
  `houses/apcoa_rates.py`; `houses/apcoa_scraper` and the script both import the
  leaf, so the houses↔scripts cycle is gone and the script's import is at the top.

#### long-param-list — 1 site — **weak**

`houses/apigw.py:203` `api_fetch(method, url, *, api, params, body, headers,
wire_params, _client_factory, _no_cache)` — 9 parameters, one caller
(`apis/base_api.py`), so "the flat signature is the ease-of-use contract" does
not hold. The doctrine's answer elsewhere is a parameter object, and here the
honest one is a class owning the guarded fetch — whose constructor then has the
same nine parameters, or a record with ≥5 fields and no methods, which trips
`record-shape` instead. **Owner call** (the fix is a small design, not a rename).

#### latent-class — 8 sites

All warnings, not fails, and every one probed with `seam_members: []`:
`attempt.py:283` (Attempt), `derived_node.py:336` (DerivedNode),
`commute_router.py:286` (CommuteRouter, 17 methods over 584 lines),
`bus_journey.py:111` (BusJourneyRegistry), `tfl_client.py:434` (TflClient),
`nodes/property_nodes.py:205` (PropertyNodes), `scripts/parse_netex_fares.py:216`
(NetexFareParser — also a failing `process-class` on a name the domain owns:
owner call), `rightmove_scraper.py:417` (`to_property`, whose advice is to move
the computation onto the record — it reads the page's DOM and preloaded state,
so it cannot). **Stands** (filed as lucidlint#47).

#### middle-man — 7 sites

`dag/measurement.py:105,111,118,125` (`__add__`/`__sub__`/`__mul__`/`__truediv__`
— each implements a protocol over the one `_binop`),
`nodes/commute.py:392` and `nodes/petrol.py:126` (`provenance_formula` — a
framework hook the DAG calls, deliberately not inlined),
`services.py:565` (`enrich` — the echo keeps the DI slot for `RailFareNode`).
**Stands** (filed as lucidlint#44); the eighth was deleted with `StampDutyFn`.

#### record-shape — 7 sites

`dag/attempt.py:310,486` (wire-format dicts at the serialization boundary —
the coding-standards exception), `tests/unit/test_deploy_allowlist.py:30` (a
subprocess env), `tests/unit/test_add_property_flow.py:34`,
`tests/unit/test_scrape_queue.py:36,43` (sqlite row projections and a claim
response — test fixtures of wire shapes),
`tests/integration/test_park_and_ride.py:26` (a provider payload fixture).
**Stands.**

#### magic-number — 5 sites

`scripts/parse_netex_fares.py:233` (the bounding-box west edge — the value IS
the data), `houses/apcoa_scraper.py:115` (a tariff-scan cap),
`houses/tfl_client.py:42`, `scripts/download_rail_fares.py:64`,
`scripts/extract_rail_fares.py:80` (the pence-per-pound factor, which the money
doctrine keeps in the money layer: `Money` owns unit math). **Stands.**

#### broad-except — 5 sites

`dag/guard.py:23,36,57,80` (`capture`/`guarded_async`/`capture_async` — the
never-raise boundary whose whole contract is returning the failure to the
caller) and `dag/derived_node.py:685` (`refresh` — compute is subclass code;
failures become pending/impossible). Each returns or records the failure; none
swallows. **Stands.**

#### special-case — 4 sites

`houses/location.py:218` (`gp=None` is the geocoder cascade signal),
`houses/scrape_queue.py:144` (a missing job row IS the absent case),
`houses/tfl_client.py:565` (a sentinel in the national-rail disambiguation),
`scripts/parse_netex_fares.py:335` (`_first_found`'s None is the shared
contract). **Stands.**

#### private-import — 4 sites

`scripts/check_restart.py:16` and `scripts/extract_bus_fares.py:26` (intra-package
helpers inside a one-shot script package), `tools/scrape_worker.py:37` and
`tests/unit/test_add_property_flow.py:18` (the cookie-minting entry point, one
per package). **Stands.**

#### loop-pipeline — 4 sites

`tools/commute/drive_isochrone.py:878`, `tools/commute/intersection.py:386`,
`tools/commute/combined_map.py:143` (group-by accumulation — no comprehension
form, and `groupby` would re-sort), `houses/web/settings_payload.py:162` (the
loop enriches each serialized POI in place; no collection is built).
**Stands.**

#### duplicate-module — 4 sites

`houses/apis/overpass_params.py:1`, `places_center.py:1`, `places_circle.py:1`,
`places_restriction.py:1` — the request DTOs are one class + one docstring + N
fields each *by design*; "reconcile the copies" is not actionable across
different request shapes. **Stands.**

#### data-clump — 3 sites

`houses/epc.py:209` (`(address, certs)` travel together), `houses/walkability.py:103`
(`(lat, lng)` is `enrich_walkability`'s public signature),
`houses/tfl_client.py:352` (the three leg-label inputs travel together through
the mode dispatch map). **Stands.**

#### unused-setter — 2 sites

`dag/scheduler.py:312` (`set_scheduler`), `houses/api_cache.py:62`
(`set_cache_dir`) — test-injection APIs called from the isolation fixtures; the
rule counts production references only. **Stands.**

#### unused — 2 sites

`dag/scheduler.py:318` (`reset_scheduler`) — a test seam the dag fixtures call to
clear the override; the alternative is tests poking `SchedulerRuntime.override`
directly. **Stands.** `houses/nodes/bootstrap.py:150` (`bootstrap_from_row`) —
the sheet-import entry point, with **no production caller at all** (startup uses
`load_property_nodes_from_db`; the `houses/sheets/` layer has no callers either).
This is not dead weight but an unwired feature: deleting it deletes the import
path's tests, wiring it is a product decision. **Owner call.**

#### swallow — 2 sites

`dag/scheduler.py:470` (`submit_to_processor` — a wedged drain must not hang
`systemctl stop`), `dag/scheduler.py:540` (`run_on_processor` — the log IS the
delivery surface for a queued callback's failure). Both log; the rule wants a
control-flow exit (quirk 1). **Stands.**

#### duplicate-block — 2 sites

`houses/location.py:476` (the outcode tier intentionally mirrors the
full-postcode tier — a two-tier cascade), `houses/nodes/property_nodes.py:235`
(extracting a helper/loop was probed: it re-fires or drops the types).
**Stands.**

#### static-husk — 1 site

`houses/apcoa_scraper.py:43` (`ApcoaScraper` is the seam type `car_park` injects
— one parse surface). **Stands.**

#### duplicate — 1 site

`houses/rail_fare_registry.py:91` — a deliberate mirror of
`get_bus_fare_reader`'s lazy-singleton idiom (a warning, and the shape is the
house idiom). **Stands.**
