# Web App — Property Dashboard with Provenance

## Problem

The enrichment engine works, but we're stuck:

**Adding a module** requires touching 7+ files (EnrichedProperty, Row.HEADERS, from_property, enrichment function, run_enrichment, View tab formulas, migrate-view). The ceremony discourages iteration.

**Computation logic is trapped in Google Sheets formulas** — stamp duty brackets, mortgage PMT, commute annualisation, total monthly cost. Raw strings, untestable, invisible to the type system.

**Provenance is impossible.** When a value is wrong, there's no way to trace: "this stamp duty was computed from price £XXX using bracket Y, and price came from Rightmove scrape at 10:30." The agent has to dig through unstructured logs to diagnose anything.

**Caching is coarse.** If the user corrects the latitude, the whole enrichment pipeline re-runs. There's no node-level cache that only recomputes the subtree affected by the change (commute, schools, walkability, station, map URL all depend on location).

**The UI is a spreadsheet** — 40-column grid designed for formula debugging, not for glancing at a property.

## Solution

A directed acyclic graph (DAG) of computation nodes. Each property value is a node with explicit dependencies. Nodes know their provenance (source API, timestamp, status, intermediate values).

The DAG's real value is **provenance and cache granularity**, not formula organisation. The formula extraction is a side effect.

### What provenance looks like

```json
{
  "id": "stamp_duty",
  "value": 10000,
  "status": "ok",
  "deps": {
    "price": {
      "value": 450000,
      "status": "ok",
      "source": "rightmove_scraper",
      "source_time": "2026-06-14T10:30:00Z"
    },
    "status": {
      "value": "For Sale",
      "status": "ok",
      "source": "manual_input",
      "source_time": "2026-06-13T15:00:00Z"
    }
  },
  "compute": {
    "formula": "stamp_duty_band(price)",
    "intermediate": {
      "band": "5% on £200k above £250k threshold",
      "raw": "max(0, min(200000, 450000-250000)) * 0.05"
    }
  }
}
```

An agent can GET `/properties/{rid}/graph?node=stamp_duty` and follow the dependency tree all the way to source APIs, without reading a single log line.

### What granular caching means

```
Enrichment (runs once, caches per node):
  rightmove_scraper ──→ price, address, postcode, bedrooms
  epc_api            ──→ epc_rating, floor_area, age_band, heating_fuel
  tfl_api            ──→ simon_commute, lorena_commute

User corrects lat/lng:
  DAG invalidates: best_lat, best_lng (direct deps)
  ↳ also invalidates: map_url, simon_commute (walk legs), lorena_commute, schools (distances)
  Everything else stays cached.

Web UI loads:
  DAG resolver walks all nodes, hits cache for everything, returns in ~0ms.
  No API calls, no JSON re-parsing.
```

### Core types

```python
class NodeResult:
    """Result of computing one node for one property."""
    value: Any = None
    status: Literal["ok", "missing", "error", "stale"]
    error: str | None = None           # traceback or message
    source: str = ""                   # "epc_api", "rightmove_scraper", "formula:stamp_duty"
    source_time: datetime | None = None
    source_status_code: int | None = None  # for API sources
    deps: dict[str, NodeResult] = {}   # recursive dependency results
    compute_info: dict | None = None   # formula name, intermediate values for derived nodes
```

```python
class ValueNode:
    id: str
    label: str
    kind: Literal["source", "derived", "manual"]
    deps: list[str]                # node IDs this depends on

    # For derived nodes: compute(dep_values) → value
    compute: Callable | None = None
    # For source nodes: which field in EnrichedProperty carries this value
    enrich_field: str | None = None

    value_type: type = str
    display: Literal["currency", "duration", "percent", "text", "badge"] = "text"
    rating_fn: Callable | None = None   # value → "good" | "warn" | "bad" | None
    group_id: str = ""
```

### Node registry

A single file `houses/model/nodes.py` that declares every node. This is the canonical list of every value the system knows about. Adding a module means adding nodes here (and writing the enrichment function, which was already needed).

### Resolver (~50 lines of orchestration)

```python
def resolve(
    property_id: str,
    enriched: EnrichedProperty | None,
    manual_inputs: dict[str, Any] | None,
    cache: NodeCache | None = None,
) -> dict[str, NodeResult]:
    """Topological-sort the DAG, compute each node in order.

    1. Build a full node dict from NODES + enriched + manual inputs
    2. Topological sort by deps
    3. Walk in order:
       - Check cache (hit → skip)
       - Check if all deps are cached (no → skip or mark stale)
       - For source nodes: value from enriched or manual_inputs
       - For derived nodes: call compute(dep_values)
       - For manual nodes: value from manual_inputs
       - Store result in cache
    4. Return flat dict of NodeResults
    """
```

### Provenance endpoint

`GET /properties/{rid}/graph?node=stamp_duty&depth=2`

Returns the `NodeResult` tree for that node, recursively including dependencies up to `depth`. An agent can start from any value and walk the tree to find root cause.

Without `node` parameter, returns the full graph for all nodes (like a schema, not values — the DAG structure itself).

---

## Phases

### Phase 1 — DAG foundation (estimated: 1.5–2 weeks)

Stage 1.1 — Define core types: `ValueNode`, `NodeResult`, `NodeCache`
Stage 1.2 — Port all sheet formulas to pure Python functions with unit tests
Stage 1.3 — Build the node registry (`houses/model/nodes.py`) declaring:
  - All enrichment fields as source nodes
  - All derived values (stamp duty, mortgage, etc.) with compute functions
  - All manual inputs (status, notes, Ashby works, actual coordinates)
  - All rating functions mapped to nodes
Stage 1.4 — Build the resolver + cache layer
Stage 1.5 — Wire into the enrichment pipeline: `run_enrichment()` → populate source nodes → `resolve()` → cache
Stage 1.6 — Build the provenance endpoint `GET /properties/{rid}/graph`

### Phase 2 — UX design (estimated: 0.5–1 week)

Before any frontend code, design the card layout:

- **Wireframes** for the property overview card showing all 5 zones (Key Info, Commute & Area, Schools, Affordability, User Inputs)
- **Information hierarchy** — which values are headline indicators vs supporting detail vs diagnostic deep-dive
- **Drill-down interaction** — click/tap a value to see its provenance: where it came from, what formula, intermediate values, source API status
- **Scoring/glanceability** — how colour indicators work for each value type
- **Error states** — how missing/errored values render (important: the provenance layer means you know *exactly* what failed)
- **Responsive breakpoints** — desktop first, mobile support for on-site viewing

Design artefacts: HTML mockups or lightweight Figma. Follow the convention from chronic-wellness `design/`.

Key questions to resolve:
- One property at a time (detail page) or scrollable list of summary cards?
- How does drill-down provenance look? Modal? Expand-in-page?
- How do manual inputs (status, notes, coordinates) get edited?
- How does a new property get added? (Keep existing browser extension flow?)

### Phase 3 — Web UI (estimated: 2–3 weeks)

Stage 3.1 — Backend: `GET /properties/{rid}/card` endpoint that:
  - Loads cached node results
  - Runs resolver for any uncached nodes
  - Returns grouped by `group_id` with rating/colour metadata
  - Returns provenance on request (`?include_provenance=true`)

Stage 3.2 — Frontend:
  - Property list page with summary cards
  - Property detail page with grouped zones and drill-down provenance
  - Manual input editing (PATCH back to the sheet)
  - Error states rendered inline (not silently swallowed)

Stage 3.3 — Validation: compare DAG output against sheet output for all existing properties. Every derived value must match.

### Sheet migration

The sheet stays. The DAG is additive:
- **Data tab**: still written by enrichment (unchanged)
- **View tab**: still updated by formula sync (legacy)
- **Web UI**: reads from DAG cache, not from sheet
- New properties enriched and written to sheet + cached via DAG

---

## Adding a module in the new model

1. Add source node(s) to `NODES` registry
2. Write the enrichment function (same as today)
3. Wire into `run_enrichment()` (same as today)
4. If there are derived values from the new data, add derived nodes with compute functions
5. Assign to a `group_id` for UI placement
6. Done — no sheet columns, no formula sync, no view migration

---

## Caching strategy

| Layer | What | Invalidates when |
|-------|------|-----------------|
| **API cache** (existing) | Raw API responses (EPC JSON, TfL routes) | TTL-based or manual clear |
| **Node result cache** | Computed node values per property | Dependency change (new lat/lng → commute nodes invalidate) |
| **Session cache** | Full property card response | Property data changes |

The node cache is a simple key-value store (`{property_id}_{node_id}` → `NodeResult`). On resolution, the resolver checks if any dependency has changed since the node was cached. If not, returns cached value. If yes, recomputes.

This means: after the initial enrichment, loading the web UI is cache hits only. Modifying a manual input (lat/lng, status) recomputes only the affected subtree.

---

## Risks

| Risk | Mitigation |
|------|------------|
| **Formula porting mis-matches** — derived values differ from sheet | Test against all existing properties. Use `/properties/compare` endpoint as oracle. |
| **Over-engineering** — too much machinery for ~8 modules | The resolver is ~50 lines. The registry is one file. The value is in provenance and caching, not in the abstraction. |
| **UX scope creep** — building a complex frontend | Limit V1 to property card + provenance drill-down. No comparison views, no cross-property dashboards. |
| **Provenance data is expensive to store** | It's computed at resolution time and served on-demand. Not persisted (except cache). |
| **Agent can't use the provenance endpoint** | The endpoint returns JSON. An agent can GET it and traverse the tree just like it reads any other API. Much easier than log parsing. |

---

## Not in scope (V1)

- Property comparison views (side-by-side)
- Cross-property dashboards or charts
- Automated "Ashby works" scoring
- Mobile app
- Real-time updates (poll or push)

---

## Decision points

Before starting Phase 2 (UX), decide:
- **Frontend stack**: server-rendered (Jinja/HTMX) vs SPA (React/Svelte)? The UX design will influence this — heavy drill-down interactivity leans SPA.
- **Where do enrichment results live?** The DAG cache could be in-memory (simple, lost on restart), SQLite (persistent, needs a sync step), or the sheet remains the source and we rebuild cache on read. SQLite is probably the sweet spot.
- **Deployment**: same FastAPI process? Separate process? Same process during development.
