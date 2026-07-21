# Wire Missing Enrichment Data into Vue Frontend

## Current State

Phases 1-6 from [the previous plan](dag-library-and-vue.md) are committed (`v1.0-full-implementation`).
What exists now:
- `dag/` library: Node, SourceNode, ComputedNode, Signal/Slot, Attempt, persistence (old tables only) — **sync only, no auto-persist, boolean dirty flag**
- `houses/nodes/location.py`: BestAddressNode, BestLocationNode — sync, work correctly
- `houses/nodes/commute.py`: CommuteSelectorNode — reads from pre-populated SourceNodes
- `houses/nodes/property.py`: PropertyNodes — 7 SourceNodes, 2 ComputedNodes, no enrichment nodes
- `houses/nodes/bootstrap.py`: bootstrap_from_row() — pushes user columns A-G to SourceNodes
- `houses/nodes/settings.py`: settings_node — module-level SourceNode, not persisted, no financial constants
- `houses/web/api_router.py`: GET /api/properties, GET /api/settings — sync endpoints
- `houses/frontend/`: Vue 3 SPA — list page shows 40 cards with addresses/RIDs only

What's wrong:
- No async compute — enrichment ComputedNodes can't call APIs
- No persistence — every value lives in RAM, vanishes on restart
- No audit trail — no record of value changes
- No enrichment ComputedNodes — commute/school/EPC/council tax aren't in the DAG
- Settings are not persisted — `settings_node` reverts to defaults on restart
- Commute pipeline is incomplete — only CommuteSelectorNode exists, no TransitNode/BusNode/intermediate nodes
- Vue shows hardcoded data — commute pills are hardcoded "30m · £4.50"

## End State

Everything is a persisted, auditable DAG node. SourceNodes hold user-entered
values and push to SQLite on every change. ComputedNodes are async (can call
APIs), compute from their deps, persist results with dependency timestamps for
cross-process staleness detection.

The full commute pipeline is decomposed into 8 intermediate nodes per
Person × POI. Schools, EPC, council tax, walkability, town description, monthly
costs are all ComputedNodes. Settings are a persisted SourceNode with financial
constants and per-POI frequency data.

The Vue frontend reads from the DAG via async API endpoints. Every field in the
detail JSON has the standard `{succeeded, value, error, provenance}` wrapper.

## Corrections from Planning Session

1. **PlaceOfInterest needs frequency data.** Each POI specifies `trips_per_week` and `weeks_per_year`. The monthly commute cost is computed as `trips_per_week × weeks_per_year × daily_cost / 12`, not a hardcoded formula.

2. **Settings SourceNode expanded and decomposed.** Splitting into separate SourceNodes to avoid triggering unnecessary recomputation when unrelated settings change:
   - `persons_source` (SourceNode) — persons, POIs with trips_per_week, bus_walk_penalty_minutes (per-person walk-to-station tolerance). Changes here trigger commute pipeline recreation.
   - `commute_thresholds_source` (SourceNode) — per-person `good_max_minutes`/`fine_max_minutes` that determine pill colours (green ≤ good_max, orange ≤ fine_max, red > fine_max). Changes here only affect Vue — no DAG recompute.
   - `financial_source` (SourceNode) — mortgage rate, term, rental income, life insurance, working weeks, current home costs. Changes here trigger monthly cost recalculation.

   Financial settings, from the current sheet's Constants tab:
   - `current_home_sale_price` — Simon&Lorena's current home value (for equity calc)
   - `current_home_outstanding_mortgage` — remaining mortgage on current home
   - Simon&Lorena's equity = current_home_sale_price - outstanding_mortgage
   - Ashby's equity = `deposit_equity` on Person Ashby (already exists on Person dataclass)
   - `mortgage_rate`, `mortgage_term_years`, `sinking_fund_rate`, `rental_income_monthly`, `life_insurance_monthly`, `working_weeks_per_year`

   No more single monolithic settings SourceNode. Each is independently persisted and has its own dependency graph.

3. **Monthly costs are sync CalculationNodes.** Mortgage payment, sinking fund, commute cost, and total monthly housing cost are pure formulas (no API calls). Each is a sync ComputedNode subclass ("CalculationNode") that depends on the relevant cost nodes and settings.

4. **Consistent JSON structure.** Every field in the API output is wrapped in `{succeeded, value, error, provenance}` — the standard Node `to_json()` output. Aggregation groups (`affordability`, `area`, `comments`, `location`, `schools`) are plain dict keys in the detail JSON, not separate Node objects. No bare values or random fields hanging off the root.

5. **Schools array in the summary.** Summary includes an array of all schools (primary + secondary) with their essential data.

6. **Every sheet column maps to a detail JSON field.** The check: if you can't find a column from the sheet in the detail JSON, something is missing.

7. **Comments group in detail.** Status, Status Reason, Group Notes, Ashby comments, Design/Planning Needed are SourceNodes (user-entered, persisted) grouped under `comments` in the detail JSON.

8. **Rental income is a setting field**, not hardcoded in the formula.

## Architecture

Everything is a persisted, auditable DAG node — SourceNodes hold user-entered
values, ComputedNodes compute derived values, both record every change to
SQLite for audit trail and fast restart. Every write records dep versions so
cross-process staleness is detectable.

Settings are decomposed into separate SourceNodes (persons, commute thresholds,
financial constants, bus_walk_penalty) so changing one doesn't trigger
recomputation of unrelated nodes. Each persists independently. No more
module-level state that vanishes on restart.

---

### Domain model changes

**`houses/model/domain.py`:**
- `PlaceOfInterest` gets `trips_per_week: int = 1` and `weeks_per_year: int = 46` — defaults mean existing code creating POIs without these still works. Most POIs are 1 trip/week; Lorena/Office overrides to 2, Simon/Dad to 0.
- Remove `Schools` wrapper class (primary/secondary are separate nodes).

### Settings changes — decomposed SourceNodes

**`houses/nodes/settings.py`:**
- Replace single `settings_node` with two independently-persisted SourceNodes:

  | SourceNode | Type | Contents | Affected downstreams |
  |---|---|---|---|
  | `persons_source` | `SourceNode[list[Person]]` | Persons with POIs + trips_per_week | Commute pipeline nodes, MonthlyCommuteCostNode |
  | `commute_thresholds_source` | `SourceNode[dict]` | `{Simon: {good_max, fine_max}, Lorena: {...}}` | Vue pill colours only (no DAG recompute) |
  | `financial_source` | `SourceNode[dict]` | `{mortgage_rate, mortgage_term_years, rental_income, ...}` | Monthly cost CalculationNodes |


- `make_default_persons()`, `make_default_thresholds()`, `make_default_financials()`, `make_default_penalty()` — separate factory functions.
- Each SourceNode persists independently. Changing `commute_thresholds` does NOT trigger commute recomputation.

```python
def make_default_persons() -> list[dict]:
    return [
        {
            "name": "Simon", "has_car": True,
            "bus_walk_penalty_minutes": 20,  # willing to walk 20min to station
            "places_of_interest": [
                {"label": "Office", "postcode": settings.simon_postcode,
                 "trips_per_week": 1, "weeks_per_year": 46},
                {"label": "Bracknell", "postcode": settings.bracknell_postcode,
                 "trips_per_week": 1, "weeks_per_year": 46},
                {"label": "Dad", "postcode": "OX7 5GZ",
                 "trips_per_week": 0, "weeks_per_year": 46},
            ],
        },
        {
            "name": "Lorena", "has_car": False,
            "bus_walk_penalty_minutes": 15,  # Lorena's value from sheet
            "places_of_interest": [
                {"label": "Office", "postcode": settings.lorena_postcode,
                 "trips_per_week": 2, "weeks_per_year": 46},
            ],
        },
    ]

def make_default_financials() -> dict[str, Any]:
    return {
        "current_home_sale_price": 0,
        "current_home_outstanding_mortgage": 0,
        # Simon&Lorena equity = current_home_sale_price - outstanding_mortgage
        "mortgage_rate": 0.045,
        "mortgage_term_years": 30,
        "sinking_fund_rate": 0.01,
        "rental_income_monthly": 0,
        "life_insurance_monthly": 0,
        "working_weeks_per_year": 46,
    }
```

### Monthly cost ComputationNodes (sync)

| Node | Formula | Deps |
|---|---|---|
| `YearlySinkingFundNode` | `price × sinking_fund_rate` | rightmove_price, financial_source |
| `MortgageRequiredNode` | `price - deposit - ashby_contribution` | rightmove_price, financial_source |
| `MonthlyMortgagePaymentNode` | `PMT(rate/12, term×12, -mortgage_required)` | mortgage_required_node, financial_source |
| `MonthlyCommuteCostNode` | `Σ (trips_week × weeks_year × daily_cost / 12)` per POI | commute_selector_nodes, persons_source |
| `TotalMonthlyHousingCostNode` | `mortgage + sinking_fund + life_ins + commute + ct - rental` | all of the above, council_tax_node, financial_source |
| `StampDutyNode` | UK stamp duty brackets | rightmove_price, status_node |
| `CommuteBreakdownNode` | daily/yearly totals from commute selectors | commute_selector_nodes, persons_source |

All sync — no API calls, pure formula.

---

## Slice 0: Persistence + Async + Timestamp staleness in dag library

**`dag/persistence.py`** (extend):

Each node persists its own `to_json()` output as a JSON blob. This means the
persisted data is exactly what the node would return — the full
`{succeeded, value, error, provenance}` dict. On reload, the node reconstructs
itself from this blob, no custom deserialization needed.

- Add `node_results` table (append-only, each write is a row):
  ```sql
  CREATE TABLE IF NOT EXISTS node_results (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      node_id TEXT NOT NULL,
      result_json TEXT NOT NULL,         -- the full to_json() output
      dep_timestamps TEXT,               -- JSON: {dep_node_id: "created_at", ...}
      created_at TEXT NOT NULL
  );
  CREATE INDEX IF NOT EXISTS idx_nr_node ON node_results(node_id, created_at DESC);
  ```
- `save_node_result(node_id, result_dict, dep_timestamps=None)` — inserts a new row.
  `result_dict` is what `to_json()` returns: `{succeeded, value, error, provenance}`.
- `latest_node_result(node_id) -> dict | None` — returns the most recent `result_dict`.
- `init_db()` creates the new table (alongside existing old ones).

**`dag/node.py`** (modify):

Every node persists its own JSON. The flow is:

1. `attempt()` computes/recomputes a result → calls `_persist()`
2. `_persist()` calls `self.to_json()` to get the full dict → saves to DB via `save_node_result()`
3. On init, `_load_from_db()` calls `latest_node_result(self._id)` — if found, deserializes the
   `result_json` dict back into the node's cached attempt. No custom deserialization needed.
4. Staleness: compare each dep's `_persisted_at` (monotonic clock) against own `_computed_at`.
   On cross-process reload, persisted `created_at` timestamps serve the same purpose.

**`dag/source_node.py`** (modify):

- `push()` calls `_persist()` after setting value — stores `to_json()` output
- `attempt()` returns immediately (no computation)
- `_computed_at` set to `time.monotonic()` on push

**`dag/computed_node.py`** (modify):

- `attempt()` is async: if stale, await deps, compute, persist result with dep timestamps
- `compute()` may return `Attempt[T]` (sync) or `Awaitable[Attempt[T]]` (async)
- `_persist()` called after each recompute, storing `to_json()` with each dep's `_persisted_at`

**Settings as decomposed persisted SourceNodes** (`houses/nodes/settings.py`):

Four independent SourceNodes, each loading from DB on init:

```python
def _make_persisted_source(node_id: str, value_type, default_factory) -> SourceNode:
    """Create a SourceNode that loads from DB or uses default."""
    from dag.persistence import latest_node_result
    node = SourceNode[node_id, value_type)
    persisted = latest_node_result(node_id)
    if persisted and persisted.get("succeeded"):
        node._value = persisted["value"]
        node._provenance = Provenance("db")
    else:
        node.push(default_factory(), Provenance("config"))
    return node

persons_source = _make_persisted_source("persons", list, make_default_persons)
financial_source = _make_persisted_source("financial", dict, make_default_financials)
```

Each SourceNode's `push()` calls `save_node_result(node_id, self.to_json(), dep_timestamps=None)`.
Loading from the sheet only happens on first run (when `latest_node_result` returns None).

The module-level `settings_node` is replaced by `persons_source` and `financial_source`.

### DB init order issue

`init_db()` is called in the server lifespan (houses/server.py). Settings
SourceNodes and `PropertyNodes.__init__()` may be imported before the lifespan
runs, so the DB may not exist yet. Solution: all persistence functions in
`dag/persistence.py` already auto-create the DB path via `_get_db()` →
`DB_PATH.parent.mkdir()`. The `init_db()` call creates the tables.

If a node calls `save_node_result()` before `init_db()`, the table won't exist.
Simplest fix: make `save_node_result()` and `latest_node_result()` call
`init_db()` internally (idempotent — `CREATE TABLE IF NOT EXISTS`).

Settings SourceNodes load from DB lazily on first `attempt()` rather than in
`__init__()`, avoiding the race:

### Startup flow — seed only once

On first startup with the new code:
1. `init_db()` creates `node_attempts` table
2. For each property in the sheet, check if `node_attempts` already has rows
   for that RID's SourceNodes (e.g. `{rid}/rightmove_address`)
3. If no rows exist (first time): read user columns A-G from sheet, push to
   SourceNodes, `_persist()` saves to DB. Also push comments columns (Status,
   Status Reason, etc.) from the View tab.
4. If rows exist (subsequent startup): skip the sheet read for that property.
   The DAG loads from persistence.
5. ComputedNodes are stale (never computed) → first `to_json()` triggers compute.

On subsequent startups (DB has data):
1. `init_db()` creates tables if not exist
2. `seed_registry_from_sheet()` creates `PropertyNodes` for each RID from the
   sheet's RID list, but does NOT push values — nodes load from DB.
3. Nodes load from `latest_node_attempt()`. SourceNodes have persisted values.
   ComputedNodes check dep timestamps: if deps' timestamps match, skip compute.
4. API call → `to_json()` → nodes return cached (or recompute if stale).

The seed is a one-time bootstrap. After the first run, the DB is the source of
truth for node values. The sheet is only read for the RID list (to know which
properties exist) and for any newly-added property's first import.

### Test isolation

New persistence tests need a temp DB to avoid polluting the real `data/dag.db`.
The existing pattern from `tests/unit/dag/test_persistence.py` replaces
`_get_db` with an in-memory SQLite connection. Follow that pattern.

For node tests that trigger persistence (calling `_persist()` or `push()`),
use `pytest.fixture(autouse=True)` that swaps `_get_db` before each test and
calls `init_db()` on the temp connection.

---

## The Commute Pipeline (DAG decomposition)

All nodes below persist their attempts. Each node's `compute()` function should
be implemented by reading the relevant existing enrichment code and wrapping it.
Before implementing a node, read its reference file(s) listed below to
understand the existing computation logic.

For each Person × PlaceOfInterest, PropertyNodes creates:

```
best_location (SourceNode[GeoPoint])
  │
  ├── TransitNode ─────────────────────────────────────────────────────────┐
  │   async → calls TfL API (via TransitRoute.plan helper).               │
  │   Deps: (best_location, poi_node)                                      │
  │   Reference: `transit_route.py:TransitRoute.plan()` — reads TfL       │
  │   journey response, parses legs + fares into Commute.                 │
  │   Persists Attempt[Commute] with dep timestamps.                       │
  │                                                                        │
  ├── WalkLegCheckNode                                                     │
  │   sync → extracts walk duration from TransitNode's first/last leg.    │
  │   Compares vs the person's bus_walk_penalty_minutes from persons_source.│
  │   Deps: (transit_node, persons_source)                                 │
  │   Reference: `routing.py:get_commute()` — walk-leg duration check.    │
  │   Returns Attempt[bool].                                               │
  │                                                                        │
  ├── BusRouteNode                                                         │
  │   async → if WalkLegCheck is true, calls Google Routes transit API    │
  │    for origin → transit station.                                       │
  │   Deps: (best_location, walk_leg_check_node, transit_node)            │
  │   Reference: `routing.py:_find_bus_alternative()` — Google Routes     │
  │   transit API call, returns CostGroup with bus legs.                  │
  │   Returns Attempt[List[CostGroup]] (empty if walk OK).                │
  │                                                                        │
  ├── BodsFareNode                                                         │
  │   sync → looks up bus origin postcode in data/bus_fares.json.         │
  │   Deps: (bus_route_node)                                               │
  │   Reference: `routing.py:_bus_fare_for()` + `bus_journey.py` —        │
  │   BusJourneyRegistry.fares_for_stops(), cheapest_round_trip().        │
  │   Returns Attempt[Money] (Money("0","GBP") if no bus leg).            │
  │                                                                        │
  ├── BusLegAugmentNode                                                    │
  │   sync → if walk too long: replaces walk leg with bus leg + BODS cost.│
  │   Deps: (transit_node, walk_leg_check_node, bus_route_node,           │
  │           bods_fare_node)                                              │
  │   Reference: `routing.py:_replace_walk_with_bus()` — merges bus       │
  │   CostGroup into transit Commute, adjusts total cost.                 │
  │   Returns Attempt[Commute].                                            │
  │                                                                        │
  ├── ParkAndRideAugmentNode  (alternative to bus leg — one or the other)  │
  │   sync → prepends drive leg to park-and-ride station.                 │
  │   Deps: (transit_node, best_location)                                 │
  │   Reference: `transit_route.py:TransitRoute._add_parking_cost()` —    │
  │   adds drive CostGroup with LegMode.PARK.                             │
  │   Returns Attempt[Commute] (or impossible if no parking).             │
  │                                                                        │
  └── CommuteSelectorNode                                                  │
      sync → picks the best of bus_augment and park_and_ride.             │
      Bus leg and park-and-ride are alternatives, not sequential.         │
      If both succeed → pick cheaper/faster.                              │
      If one succeeds → use it.                                           │
      Deps: (bus_leg_augment_node, park_and_ride_augment_node)            │
      Returns Attempt[Commute].                                            │
```

All are proper ComputedNode subclasses created in `PropertyNodes.__init__()`.
Not constructed on-the-fly from settings — they're created once and wired
via deps. Settings changes propagate through the dep graph (if a POI postcode
changes, TransitNode becomes stale and recomputes).

---

## The School Pipeline

```
best_location (SourceNode[GeoPoint])
  │
  ├── PrimarySchoolNode (sync)                                             │
  │   Loads data/edubaseall_enriched.csv, finds nearest primary.           │
  │   Deps: (best_location)                                                │
  │   Returns Attempt[School].                                             │
  │                                                                        │
  ├── PrimarySchoolCommuteNode (async)                                     │
  │   Calls Google Routes walking API. If walk > threshold (settings),    │
  │   falls back to Google Routes transit/bus. Returns mode=WALK or BUS.  │
  │   Deps: (best_location, primary_school_node, persons_source)           │
  │   Returns Attempt[Commute].                                            │
```

Same pattern for secondary school: `SecondarySchoolCommuteNode`.

---

## Enrichment Nodes (Detail Page)

| Node | Deps | API | Compute |
|---|---|---|---|
| `EpcNode` | best_address | UK EPC API (async) | lookup_epc(postcode, address) |
| `CouncilTaxNode` | best_address | VOA + CivAccount (async) | council_tax.lookup(postcode) |
| `GeocodeNode` | best_address | Google/ORS/Nominatim (async) | _geocode_address(address) |
| `WalkabilityNode` | best_location | ORS walk + Google Places + Overpass (async) | walkability.enrich(lat, lon) |
| `TownDescNode` | best_location | OpenRouter LLM (async) | town_desc.generate(postcode) |

Each:
- Extracts needed values from deps
- Calls the existing enrichment function (which handles api_cache as side effect)
- Returns `Attempt[T]`
- Persists result with dep timestamps

---

### Comments SourceNodes

The View tab has user-entered columns that need SourceNodes:
- `status` (SourceNode[str]) — "Consider", "Book Viewing", "Current", "Dismissed"
- `status_reason` (SourceNode[str])
- `group_notes` (SourceNode[str]) — "Group Notes / WhatsApp" column
- `ashby_comments` (SourceNode[str]) — "Ashby comments" column
- `ashby_works_estimate` (SourceNode[float])
- `design_needed` (SourceNode[str]) — yes/no dropdown
- `planning_needed` (SourceNode[str]) — yes/no/yikes dropdown

These are added to `PropertyNodes.__init__()` and pushed during
`bootstrap_from_row()` from the matching sheet columns. They're grouped under
`comments` in the detail JSON.

## Detail JSON Shape

Every column from the sheet must appear in the detail JSON. The detail is
organised into aggregation groups matching the sheet's logical sections:

```jsonc
{
  "rid": "88275093",

  // Top-level identity fields
  "best_address": { "succeeded": true, "value": "31 Isambard Road, Southall, UB2 4GN", ... },
  "rightmove_url": { ... },
  "rightmove_price": { ... },
  "rightmove_bedrooms": { ... },
  "postcode": { ... },

  "location": {
    "best_location": { "lat": 51.5013, "lon": -0.3687, ... },
    "precise_location": { ... },
    "rightmove_location": { ... },
    "geocode": { ... },
    "approx_lat": { ... },
    "approx_lng": { ... },
    "approx_station_crs": { ... },
    "approx_station_name": { ... },
    "map_url": { ... }
  },

  "commutes": {
    "Simon/Office": { ... },   // same structure — full commute pipeline
    "Simon/Bracknell": { ... },
    "Lorena/Office": { ... },
    "Town": {                   // walkability — Commute object, keyed by town name
      "commute": { "succeeded": true,
        "value": { "duration_minutes": 12, "mode": "WALK", ... }, ... }
    }
  },

  "schools": {
    "primary": {
      "school": { "succeeded": true, "value": { "name": "St Mary's...", "ofsted": "Good", ... }, ... },
      "commute": { "succeeded": true, "value": { "duration_minutes": 8, "mode": "WALK", ... }, ... }
    },
    "secondary": {
      "school": { ... },
      "commute": { ... }
    }
  },

  "affordability": {
    "council_tax": { "band": "D", "yearly_cost": 2002.81, ... },
    "monthly_mortgage": { ... },
    "monthly_sinking_fund": { ... },
    "monthly_life_insurance": { ... },
    "monthly_commute_cost": { ... },       // CalculationNode: 46wk × (bracknell + simon + 2×lorena) / 12
    "monthly_council_tax": { ... },
    "total_monthly_housing_cost": { ... }, // CalculationNode: mortgage + sinking_fund + life_ins + commute + ct
    "commute_breakdown": {
      "simon_daily_gbp": 4.50,
      "lorena_daily_gbp": 7.20,
      "bracknell_daily_gbp": 3.80,
      "yearly_total_gbp": 1218.60,
      "formula_explanation": "46wk x (3xBracknell_daily + 2xLorena_daily + 1xSimon_daily)"
    }
  },

  "area": {
    "walkability": { "succeeded": true,
      "value": {
        "amenities": "Supermarket (3m), Park (5m)"
      },
      ...
    },
    "town_description": { ... }  // TownDescNode
  },

  "comments": {
    "status": { "succeeded": true, "value": "Consider", ... },
    "status_reason": { ... },
    "group_notes": { ... },
    "ashby_comments": { ... },
    "ashby_works_estimate": { ... },
    "design_needed": { ... },
    "planning_needed": { ... }
  },

  "settings": {
    "persons": [ ... ],       // each person has bus_walk_penalty, good_max, fine_max
    "financial": { ... }
  }
}
```

The summary JSON (list page cards) is a flattened subset of the same fields:

```jsonc
{
  "rid": "88275093",
  "best_address": { ... },
  "best_location": { ... },
  "rightmove_price": { ... },
  "rightmove_bedrooms": { ... },
  "commutes": {
    "Simon/Office": { "commute": { ... } },       // only the final CommuteSelectorNode
    "Simon/Bracknell": { "commute": { ... } },
    "Lorena/Office": { "commute": { ... } }
  },
  "schools": {
    "primary": { "school": { ... }, "commute": { ... } },
    "secondary": { "school": { ... }, "commute": { ... } }
  },
  "total_monthly_cost": { ... },
  "walkability": { ... }    // includes town_commute: { duration_minutes, mode, ... }
}
```

## Monthly Housing Cost — CalculationNode

The monthly cost formula from `formulas.py`:

```
monthly_council_tax = council_tax.yearly_cost / 12 (if > 0, else "")
monthly_commute_cost = 46wk × (bracknell_cost + simon_cost + 2×lorena_cost) / 12
monthly_mortgage = from sheet column
monthly_sinking_fund = yearly_sinking_fund / 12 × 2/3
monthly_life_insurance = const (from settings)

gross_total = mortgage + sinking_fund + life_insurance + commute_cost + council_tax
total = gross_total - rental_income (if status == "Current")
```

This is a derived calculation with no API calls. It should be a ComputedNode
(or "CalculationNode" — a sync ComputedNode subclass for pure formulas) that
depends on the individual cost nodes.

The `commute_breakdown` (daily costs + yearly total) already exists as
`compute_commute_breakdown()` in `houses/enricher.py`. The new DAG wraps it
in a sync ComputedNode that depends on the three commute selector nodes.

Result: a `TotalMonthlyHousingCostNode` (sync ComputedNode[float]) and a
`CommuteBreakdownNode` (sync ComputedNode[CommuteBreakdown]) that are part
of the `affordability` group.

## PropertyNodes: delegates to aggregation classes

**`houses/nodes/property.py`** (full rewrite):

PropertyNodes creates all individual nodes, passes them to aggregation classes,
and delegates JSON production. Each aggregation class (`LocationGroup`,
`CommutePipeline`, `SchoolsGroup`, `AffordabilityGroup`, `AreaGroup`,
`CommentsGroup`) owns its own `to_json()`, `to_json_summary()`, and
`to_json_detail()` methods that compose output from their child nodes.

No `to_json_detail()` in PropertyNodes reaches into individual nodes — it
only calls aggregation classes' `to_json()` methods.
```

---

## API Endpoints

```python
@api_router.get("/properties/{rid}")
async def get_property_summary(rid):   → await prop.to_json_summary()

@api_router.get("/properties/{rid}/detail")
async def get_property_detail(rid):    → await prop.to_json_detail()

@api_router.get("/properties/all")
async def get_all_properties():         → all summaries

@api_router.patch("/properties/{rid}/address")
async def patch_address(rid, body):    → prop.corrected_address.push() → persist → broadcast

@api_router.patch("/properties/{rid}/location")
async def patch_location(rid, body):   → prop.precise_location.push() → persist → broadcast

@api_router.get("/settings")
async def get_settings():               → {persons: persons_source.attempt(), financial: financial_source.attempt()}

@api_router.patch("/settings/persons")
async def patch_persons(body):         → persons_source.push() → persist → broadcast

@api_router.patch("/settings/financial")
async def patch_financial(body):       → financial_source.push() → persist → broadcast
```

---

## Testing

Existing tests already verify computation logic (TfL parsing, BODS fare matching,
EPC cert matching, etc.) via direct function calls with mocked HTTP.

New node tests verify DAG integration:
- `@pytest.mark.asyncio async def` tests
- Use `mock_httpx()` from integration conftest for async nodes
- Sync nodes: push to SourceNode → `await node.attempt()` → assert
- Persistence: push → verify DB has row → restart (new instance) → verify loads from DB
- Staleness: push new source → verify node recomputes
- Settings: verify survives a `SettingsNode()` re-instantiation

---

## Slices

| Slice | What | Key files | Depends on |
|---|---|---|---|
| 0a | `node_results` table, `save_node_result()`, `latest_node_result()`. Test DB isolation fixture. | `dag/persistence.py`, `tests/unit/dag/conftest.py` | — |
| 0b | Async `attempt()`/`to_json()` on base Node + SourceNode + ComputedNode. Timestamp staleness. `_persist()` saves `to_json()` output. | `dag/node.py`, `dag/source_node.py`, `dag/computed_node.py` | 0a |
| 0c | Decomposed settings SourceNodes. Each loads from DB on init, falls back to factory defaults. Persons, commute_thresholds, financial, bus_walk_penalty. | `houses/nodes/settings.py`, `houses/model/domain.py` | 0b |
| 1a | `PlaceOfInterest` domain change + `make_default_settings()` with financial constants | `houses/model/domain.py`, `houses/nodes/settings.py` | — |
| 1b | Async ComputedNodes: GeocodeNode + TransitNode + WalkLegCheckNode | `houses/nodes/geocode.py`, `houses/nodes/transit.py` | 0b, 1a |
| 2 | Async + sync nodes: BusRouteNode + BodsFareNode + BusLegAugmentNode | `houses/nodes/bus.py` | 1b |
| 3 | ParkAndRideCheckNode + ParkAndRideAugmentNode + CommuteSelectorNode (update deps) | `houses/nodes/commute.py` | 2 |
| 4 | School nodes (primary + secondary + walk + bus) | `houses/nodes/schools.py` | 0b |
| 5 | EPC, CouncilTax, Walkability, TownDesc, CalculationNodes (monthly costs) | `houses/nodes/epc.py`, `houses/nodes/council_tax.py`, etc. | 0b, 1a |
| 6 | PropertyNodes full rewrite + bootstrap comments + summary/detail | `houses/nodes/property.py`, `houses/nodes/bootstrap.py` | 1a, 3, 4, 5 |
| 7 | Comments SourceNodes in bootstrap + PropertyNodes. Async API endpoints. | `houses/web/api_router.py`, `houses/nodes/bootstrap.py` | 6 |
| 8 | Vue list page — summary cards from real data. Cross-check against sheet. | `houses/frontend/src/` | 7 |
| 9 | Vue detail page — all sections. Cross-check every column against sheet. | `houses/frontend/src/` | 7 |
| 10 | Settings UI + address/location editing in Vue | `houses/frontend/src/`, `houses/web/api_router.py` | 8, 9 |

## Cross-Check Verification

After Slices 8 and 9 (Vue list/detail pages), cross-check the API output
against the sheet's calculated values:

1. For a known property (`GET /api/properties/{rid}/detail`), compare each
   field against the matching sheet column:
   - Commute durations: does the TfL API give the same minutes as the sheet?
     If different, is the best_location different? (our geocode may differ)
   - School names and Ofsted: same school? If different, has the CSV changed
     (the sheet data may be stale)?
   - EPC rating: same band? EPC API should be authoritative.
   - Council tax band and cost: same band/cost?
   - Monthly total: compare against the sheet's formula result. Differences
     in formula constants or input values will cause differences — document why.
   - Coordinates: compare `best_location` against the sheet's "Best Latitude" /
     "Best Longitude". Our geocode may differ from the sheet's formula
     (`precise > approx > geocode` chain).

2. For coordinates specifically: if our `best_location` differs from the
   sheet's, check whether the new value is realistic. Open the coords in
   Google Maps and verify they point to the property's street. If the sheet
   had wrong coords (common with postcode-centroid geocoding), our new value
   may be more accurate — but verify.

3. After each verification, document any expected differences in a comment
   in the implementation commit. This makes it clear which differences are
   intentional and which are bugs.

## Coding Standards: DAG Node Architecture

*Written for an agent adding new fields to the enrichment pipeline — e.g.
extracting more EPC data to estimate heating costs, or adding a new enrichment
source. Follow these rules so your nodes integrate correctly with the rest of
the DAG and the Vue frontend.*

### Architecture Overview

The system is a reactive DAG: SourceNodes hold user-entered values,
ComputedNodes produce derived values. Nodes connect via Signal/Slot — when a
value changes, signals propagate through the dependency graph and downstream
nodes recompute. Every node persists its own output to SQLite so values survive
restarts. The API returns node `to_json()` output — always wrapped in
`{succeeded, value, error, provenance}`.

### Node Types

| Type | What it holds | How value arrives | Examples |
|---|---|---|---|
| `SourceNode[T]` | User-entered or externally-scraped value | `.push(value, provenance)` | rightmove_address, corrected_address, precise_location |
| `ComputedNode[T]` | Value computed from dependencies | `compute(*dep_attempts)` runs when stale | BestAddressNode, TransitNode, TotalMonthlyCostNode |

There is no third type. If you need to compute something, it's a ComputedNode.
If the computation involves multiple intermediate steps, each step is its own
ComputedNode.

### How to Add a New Field

Suppose you want to add "estimated yearly heating cost" from the EPC API.
The EPC API already returns floor area, heating fuel type, and energy
consumption — you just need to extract and compute.

**Step 1: Check what the API already returns.**

Read the enrichment function (e.g. `epc.lookup_epc()`). See what fields are
in the API response that we don't currently expose. The EPC API returns
`currentEnergyConsumption`, `floorArea`, `mainHeatingFuel`, `mainHeatSource`,
etc. — but `EpcNode` currently only outputs the band rating.

**Step 2: Create a new ComputedNode (or extend an existing one).**

If the computation requires new API data that the existing node doesn't fetch,
add it to the existing node's parsing. If it's purely a formula on existing
node output (e.g. heating_cost = floor_area × unit_price × consumption_factor),
create a new CalculationNode that depends on the existing nodes.

```python
class YearlyHeatingCostNode(ComputedNode[float]):
    """Estimated yearly heating cost from EPC data and fuel price settings."""

    def __init__(self, node_id: str, *, epc_node, financial_source):
        super().__init__(node_id, float, (epc_node, financial_source))

    def compute(self, epc: Attempt[dict], fin: Attempt[dict]) -> Attempt[float]:
        if not epc.is_succeeded:
            return self._impossible({"epc_node": epc})
        epc_val = epc.value_or_none()
        energy = epc_val.get("current_energy_consumption")
        floor = epc_val.get("floor_area")
        fuel = epc_val.get("main_heating_fuel")
        if not all([energy, floor, fuel]):
            return self._impossible(
                {"epc_node": epc},
                extra=f"missing fields: energy={energy} floor={floor} fuel={fuel}"
            )
        fuel_price = _FUEL_PRICES.get(fuel)  # from settings or a local mapping
        yearly_cost = round(energy / floor * fuel_price, 2)
        return Attempt.succeeded(
            yearly_cost,
            Provenance("formula:heating_cost",
                       sources={"epc_data": epc, "fuel_price": fuel_price}),
        )
```

**Step 3: Register the new node in PropertyNodes.**

```python
# In PropertyNodes.__init__:
self.heating_cost = YearlyHeatingCostNode(
    f"{self.rid}/heating_cost",
    epc_node=self.epc,
    financial_source=financial_source,
)

# Add to _all_nodes for signal wiring:
self._all_nodes.append(self.heating_cost)

# Add to to_json_detail under affordability:
"affordability": {
    ...
    "heating_cost": await self.heating_cost.to_json(),
}
```

**Step 4: Add the TypeScript type.**

In `houses/frontend/src/types/index.ts`, add the field to `PropertyDetail`:

```typescript
export interface PropertyDetail {
  ...
  affordability: {
    heating_cost: AttemptValue<number>
    ...
  }
}
```

**Step 5: Write tests.**

Follow the test patterns from the Testing section. Test:
- Node computes correctly when deps have values
- Node returns impossible when deps fail
- Staleness: push new value to dep → node recomputes
- Persistence: after compute, verify DB has the `to_json()` output

### Rules for ComputedNodes

1. **One intermediate value = one node.** If a computation has steps A → B → C,
   create `StepANode`, `StepBNode(deps=[StepANode])`, `StepCNode(deps=[StepBNode])`.
   Never combine multiple computation steps into one compute() function.

2. **`compute()` is pure.** It reads only from dep attempts and class-level
   constants. No side effects (no API calls outside the designated async
   functions, no file I/O, no mutation of global state). API calls happen in
   async nodes and must use `houses.api_cache.get_cached/set_cached` so
   results survive restarts.

3. **`compute()` must handle dep failure.** Every dep attempt could be
   `is_succeeded=False`. Check each dep and call `self._impossible()` with
   clear error messages:

   ```python
   def compute(self, dep1: Attempt[X], dep2: Attempt[Y]) -> Attempt[Z]:
       if not dep1.is_succeeded or not dep2.is_succeeded:
           return self._impossible({"dep1": dep1, "dep2": dep2})
   ```

4. **`compute()` may be sync or async.** If it calls an API, it's async.
   If it's a pure formula, it's sync. The base class handles both:

   ```python
   async def compute(self, ...) -> Attempt[Commute]:  # async — calls API
   def compute(self, ...) -> Attempt[float]:           # sync — pure formula
   ```

5. **Do not access other nodes' internals.** The only way to read another
   node's value is via dep attempts passed to `compute()`. Never call
   `some_node._value` or `some_node.attempt()` directly — the dependency
   must be declared in `deps` so the signal chain works correctly.

   Exception: module-level settings SourceNodes (`persons_source`,
   `financial_source`, etc.) are accessed via their `.attempt()` method
   because they are stable global nodes that are always available. But even
   then, declare them in `deps` if the computation depends on their values.

### Rules for SourceNodes

1. **SourceNodes hold only user-entered values.** Address, postcode, bedrooms,
   price, actual lat/lng, corrected address, status, comments — anything a
   user types or a browser extension injects.

2. **Every `push()` persists automatically.** You never need to call
   `save_node_result()` manually. The base class handles it.

3. **SourceNodes load from DB on creation.** If the DB has a stored value,
   the node uses it. If not, the node starts empty (returns impossible on
   `attempt()`). Bootstrap code calls `push()` to populate on first run.

4. **Do not create a SourceNode for computed data.** If a value can be derived
   from other values, it must be a ComputedNode. The only exception is data
   from external scraping (Rightmove listing) that arrives via the browser
   extension — those are pushed by the enrichment pipeline, but they're still
   user-originated.

### Persistence Rules

1. **Every node persists its own `to_json()` output.** The `_persist()` method
   in the base Node calls `save_node_result(node_id, self.to_json())`. Never
   persist a raw value — always persist the full `{succeeded, value, error,
   provenance}` dict.

2. **On restart, nodes load from DB.** `latest_node_result(node_id)` returns
   the stored `to_json()` dict. The node reconstructs its cached attempt from
   it. No custom deserialization.

3. **Staleness is timestamp-based.** Each node's `_computed_at` is compared
   against deps' `_persisted_at`. If any dep has a newer timestamp, the node
   recomputes. On cross-process reload, the DB `created_at` timestamps serve
   the same purpose.

4. **Do not bypass the DB.** Never read calculated values from the Google
   Sheet. The DB is the only persisted source of node values after bootstrap.

### JSON Structure Rules

1. **Every field is wrapped.** Every value in every API response has the shape:
   ```json
   { "succeeded": true, "value": <the actual value>, "error": null,
     "provenance": { "label": "TfL API", "timestamp": "..." } }
   ```
   There are no bare strings, numbers, or objects at any level.

2. **Aggregation groups are just keys, not separate nodes.** The detail JSON
   uses dict keys like `"affordability"`, `"area"`, `"location"` to group
   related nodes. Each sub-value inside the group still has the standard
   wrapper. These groups are assembled in `to_json_detail()` — they are not
   Node subclasses.

3. **Every sheet column maps to a JSON field.** When adding a new field, check
   `houses/sheets/row.py:Row.HEADERS` and `houses/sheets/formulas.py` for the
   sheet column it corresponds to. Every column should appear somewhere in
   the detail JSON. If there's no match, either the data isn't being
   extracted or a field is missing.

### Dependency Wiring Rules

1. **Deps are declared in `__init__`, not in `compute()`.** Pass dependent
   nodes as constructor kwargs:

   ```python
   class MyNode(ComputedNode[float]):
       def __init__(self, node_id: str, *, dep_a, dep_b, dep_c):
           super().__init__(node_id, float, (dep_a, dep_b, dep_c))
   ```

   The base class subscribes to each dep's `changed` signal automatically.

2. **Signals propagate automatically.** When a SourceNode pushes a value, it
   calls `changed.emit()`. Subscribing ComputedNodes become stale and
   recompute on next `attempt()`. Do not manually wire signals between
   nodes unless you need to trigger something on change without depending
   on the value (rare).

3. **Settings SourceNodes are wired as deps when the computation depends on
   them.** Example: WalkLegCheckNode depends on `transit_node` (for the walk
   duration) and `persons_source` (for the person's bus_walk_penalty). The
   dependency is explicit so the node recomputes when either changes.

### Settings Patterns

1. **Decompose by recomputation scope.** Settings that affect different parts
   of the graph go in separate SourceNodes:
   - `persons_source` — affects commute pipelines and monthly costs
   - `commute_thresholds_source` — affects Vue only (pill colours), no DAG recompute
   - `financial_source` — affects monthly cost CalculationNodes
   - One setting change should not trigger unrelated recomputation.

2. **Per-person settings live in the person dict.** bus_walk_penalty,
   trips_per_week, weeks_per_year, has_car, deposit_equity all live under
   each person entry in `persons_source`.

3. **Financial constants are in `financial_source`.** mortgage_rate, term,
   rental_income, life_insurance, working_weeks, current home values.
   Not hardcoded in formulas.

### Testing Patterns

1. **Use `@pytest.mark.asyncio`** for all node tests (attempt/to_json are async).

2. **Mock HTTP at the transport layer** for real-API nodes (TransitNode,
   EpcNode, etc.). Use `mock_httpx()` from `tests/integration/conftest.py`.

3. **Test the DAG boundary, not the computation logic.** Existing tests in
   `tests/unit/test_transit_route.py`, `tests/unit/test_routing.py`, etc.
   already verify the computation. Your node test verifies that:
   - Node creates with right deps
   - `await node.attempt()` returns the right Attempt
   - Dep failure → `Attempt.impossible` with dep name in error
   - `to_json()` has the standard wrapper shape

4. **Test persistence isolation.** Use the in-memory SQLite pattern from
   `tests/unit/dag/test_persistence.py` to avoid writing to the real DB.

5. **Test staleness.** Push a new value to a SourceNode dep, verify the
   downstream ComputedNode recomputes on next `await node.attempt()`.

### What NOT to Do

- ❌ Read calculated values from the sheet. User-entered columns only (A-G
  plus View tab user columns). Everything else is a ComputedNode.
- ❌ Create ad-hoc calculations in signal handlers or constructors. Every
  computation that depends on a node's value must be in a ComputedNode.
- ❌ Access `node._value` or `node._cached` externally. Use `node.attempt()`.
- ❌ Persist raw values. Always persist the full `to_json()` dict.
- ❌ Hardcode constants in formulas. Use the settings SourceNodes.
- ❌ Add bare fields to the API response. Every value gets the standard wrapper.
- ❌ Modify enrichment modules (`transit_route.py`, `routing.py`, `epc.py`,
  etc.), sheet modules (`houses/sheets/`), or the old `houses/model/` DAG.
  New code goes in `houses/nodes/` or as shared helpers.

## Files Never Modified

- `houses/enrichment_runner.py`, `houses/transit_route.py`, `houses/bus_journey.py`,
  `houses/routing.py`, `houses/epc.py`, `houses/council_tax.py`, `houses/walkability.py`,
  `houses/town_desc.py`, `houses/schools.py`, `houses/location.py` — unchanged.
  We may extract shared helpers but don't touch existing logic.
- `houses/sheets/` — unchanged.
- `houses/model/` (old DAG) — unchanged.
