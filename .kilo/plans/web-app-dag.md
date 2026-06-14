# Houses Web App

## 1. What we're building and why

### Who are the users?

**Primary user:** Ashby (me). Non-technical house hunter. Currently bribses a 40-column Google Sheet with conditional formatting, tries to view it on a phone, and gives up. Wants to glance at a property card and immediately understand whether it's worth pursuing.

**Secondary user:** Lorena (partner). Also non-technical. Needs to see commute info, schools, and affordability at a glance without understanding the spreadsheet structure.

**Tertiary user:** Simon (agent, both AI and future human). Needs to debug why a particular value is what it is, trace it back to its source API, and understand what fallback logic was applied — without reading log files or re-running enrichment.

### What's the problem?

**The spreadsheet is the only interface.** It's powerful but:
- **Not glanceable.** 40 columns, dense grid. You can't see what a property is like without reading every cell.
- **Not mobile-friendly.** Spreadsheet on a phone is unusable.
- **No provenance.** When a value looks wrong, there's no way to see where it came from, what API was called, what it returned, or what fallback was taken.
- **Adding a new data module requires touching 7+ files** (EnrichedProperty, Row.HEADERS, Row.from_property, enrichment function, run_enrichment, View tab formulas, migrate-view deployment). The ceremony discourages adding useful data.

### What does success look like?

A web app where:
- Ashby opens a property page on their phone and immediately sees: price, EPC rating (colour-coded), commute times, total monthly cost. All glanceable.
- Tapping any value shows how it was calculated and where the data came from.
- Lorena can check commute info without opening the spreadsheet.
- An AI agent can GET `https://houses/properties/12345/graph?node=stamp_duty` and get back a JSON tree showing exactly how that value was computed, what APIs were called, and whether anything went wrong.
- Adding a new data source (crime stats, planning applications) means writing the enrichment function and declaring what it produces — no spreadsheet columns, no formula sync, no view migration.

---

## 2. Technical approach

Two parallel tracks:

**Track A — Card UI (immediate value).** Build a web UI that reads from the existing Google Sheet. The sheet already has all the data (commute, schools, EPC, council tax, affordability calculations). The UI groups it into glanceable cards with colour indicators and expandable sections. Mobile-responsive. This delivers value in weeks, not months.

**Track B — DAG data model (behind the scenes).** Define every enrichment value as a node in a computation graph. Nodes know their dependencies, their compute logic, and their provenance. Results are cached in SQLite. The UI reads from SQLite when available, falls back to the sheet. Modules are migrated from sheet → DAG one at a time. New modules go directly on the DAG.

The sheet stays the source of truth until every module is migrated. Enrichment always writes to the sheet (unchanged). Dual-write to SQLite is added alongside.

### Why a DAG?

A computation graph (nodes with explicit dependencies and compute functions) solves three problems:

1. **Provenance.** Every node result records its source (API name, formula name, manual input), when it was computed, which API fallback was taken, what intermediate values were used, and any errors. This is captured at compute time and persisted. An agent can query any value months later and see exactly what happened.

2. **Cache granularity.** When an input changes (e.g. user corrects the latitude), only the affected subtree recomputes (commute, schools, walkability, map URL). Everything else stays cached.

3. **Module addition.** Add a node declaration + write the enrichment function. No sheet columns, no formula sync, no view migration.

### Core types

```python
@dataclass
class NodeDef:
    """Schema for one node in the computation graph."""
    id: str
    label: str
    kind: Literal["source", "derived", "manual"]
    deps: list[str] = []              # node IDs this depends on
    compute: Callable | None = None   # for derived nodes: fn(dep_values...) → value
    enrich_field: str | None = None   # for source nodes: which EnrichedProperty field
    display: Literal["currency", "duration", "percent", "text", "badge"] = "text"
    rating_fn: Callable | None = None # value → "good" | "warn" | "bad" | None
    group_id: str = ""
    description: str = ""             # what this value means, what could go wrong
```

```python
@dataclass
class NodeResult:
    """Result of computing one node for one property.

    Captured at compute time. Persisted in SQLite. An agent can query
    this months later and see exactly what happened.
    """
    value: Any = None
    status: Literal["ok", "missing", "error"] = "missing"
    error: str | None = None          # traceback or message
    source: str = ""                  # "epc_api", "rightmove_scraper", "formula:stamp_duty"
    source_time: datetime | None = None
    source_status_code: int | None = None
    dep_ids: list[str] = field(default_factory=list)  # references to deps (not recursive)
    compute_info: dict | None = None  # formula name, input values, intermediate steps, fallback path
```

### Node registry

A single file `houses/model/nodes.py`. Every node the system knows about, declared top to bottom. Adding a module means adding nodes here.

```python
NODES: dict[str, NodeDef] = {}

def node(id, label, kind, *, deps=None, compute=None, enrich_field=None, ...):
    NODES[id] = NodeDef(...)

# ── Source nodes (from enrichment) ──
node("price", "Purchase Price", "source", enrich_field="price",
     display="currency", group_id="key_info",
     description="From Rightmove listing. Sometimes needs manual correction.")

node("epc_rating", "EPC Rating", "source", enrich_field="epc_rating",
     display="badge", group_id="key_info",
     rating_fn=_epc_colour,
     description="A-G band from Energy Performance Certificate API.")

node("simon_commute_time", "Simon Commute (min)", "source",
     enrich_field="simon_commute.duration_minutes",
     display="duration", group_id="commute",
     description="From TfL API. Falls back to National Rail fares if TfL unavailable.")

# ... all source nodes

# ── Derived nodes (computed formulas) ──
node("stamp_duty", "Stamp Duty", "derived",
     deps=["price", "status"],
     compute=lambda price, status: 0 if status == "Current" else _calc_stamp_duty(price),
     display="currency", group_id="affordability",
     description="UK stamp duty. Returns 0 for properties already owned.")

node("total_monthly_housing", "Total Monthly Housing Cost", "derived",
     deps=["mortgage_payment", "sinking_fund", "commute_cost", "council_tax", "status"],
     compute=_total_housing_cost,
     display="currency", group_id="affordability")

# ... all derived nodes
```

### Resolver

~90 lines. Topological sort (Kahn's), then walk in order. For each node:
- Check cache (hit → skip)
- Check if all deps are cached (no → mark stale)
- Source nodes: extract from EnrichedProperty
- Derived nodes: call compute function with resolved dep values
- Manual nodes: value from user input
- Wrap in NodeResult with provenance (source, timestamp, status, compute_info, error on failure)

See `houses/model/resolver.py` for the implementation.

### Persistence

SQLite, single table:

```sql
CREATE TABLE node_results (
    property_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    result_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (property_id, node_id)
);
```

All node results for a property are loaded at once and traversed via Python dict lookups (40 nodes × 100 properties = 4000 rows, ~2ms load time). No graph database needed.

### Graph endpoint

`GET /properties/{rid}/graph?node={node_id}&depth={n}`

Returns the NodeResult for that node, with dependencies recursively resolved up to `depth`. The agent walks this tree to debug without logs:

```
Agent: → GET /properties/123/graph?node=total_monthly_housing&depth=3
       → sees commute_cost.deps.simon_cost.source = "nr_fare_fallback"
         and compute_info.path = "TfL returned 402 → NR fare fallback"
       "That's why Simon's cost is 0. TfL was out of credits."
```

### Retrospective debugging

The agent doesn't know when the user looked at a property. Logs by timestamp are useless. The graph endpoint returns the exact state at enrichment time because `NodeResult` is persisted in SQLite and includes `source_time`. The agent doesn't reproduce the bug — they read what happened.

---

## 3. Thin slices

### Slice 1 — Card UI backed by the sheet

Build a web app that reads from the existing Google Sheet via `houses/sheets/reader.py`.

**Pages:**
- Property list: scrollable summary cards with price, bedrooms, EPC (coloured), commute summary, status
- Property detail: full card with 5 zones (Key Info, Commute, Schools, Affordability, User Inputs)
- Mobile-responsive (this is the main win over the spreadsheet)

**Delivers:** glanceable, mobile-friendly view. Zero backend changes. The sheet stays the source of truth.

### Slice 2 — Port one module to DAG + SQLite

Pick one enrichment module (e.g. EPC — self-contained, has Tier 1 data waiting to be added).

**Changes:**
- Add core DAG types, resolver, node registry
- Define EPC nodes (rating, floor area, age band, heating fuel)
- Port any sheet formulas to Python
- Write EPC results to SQLite during enrichment (dual-write with sheet)
- UI reads EPC from SQLite, falls back to sheet

**Delivers:** new EPC fields appear without sheet columns. Provenance exists for the first time.

### Slice 3 — Provenance endpoint

`GET /properties/{rid}/graph` for the ported module. Agent can debug. UI can show "why this value?"

### Slice 4 — Port commute + schools

The most valuable provenance target (TfL fallbacks, rate limits, NR fare fallback). The most data (Simon/Lorena/Bracknell + two schools).

### Slice 5 — Add a NEW module directly on DAG

Crime + air quality, or planning API, or any Tier 2 Houses item. Goes directly into the DAG. No sheet column, no formula sync, no view migration. **This is the payoff** — adding a module without the 7-file ceremony.

### Slice 6 — Port remaining modules

Walkability, town description, council tax, geo, formulas (stamp duty, mortgage, commute annualisation, total monthly cost). Sheet fallback shrinks.

### Slice 7 (optional) — Sheet as legacy archive

If every field is in SQLite and the UI handles manual edits, the sheet becomes a readable backup.

---

## 4. Adding a module in the new model

1. Add `node(...)` entries to `houses/model/nodes.py`
2. Write the enrichment function (same as today)
3. Wire into `run_enrichment()` (same as today)
4. If there are derived values, add derived nodes with compute functions
5. Done — no sheet columns, no formula sync, no view migration
