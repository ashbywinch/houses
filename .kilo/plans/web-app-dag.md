# Web App — DAG-Driven Property Dashboard

## Problem

The rightmove enrichment engine works, but:

- **Adding a module requires touching 7+ files**: `EnrichedProperty`, `Row.HEADERS`, `Row.from_property()`, enrichment function, `run_enrichment()`, View tab formulas, `migrate-view` deployment. The ceremony discourages iteration.
- **Derivation logic lives in Google Sheets formulas** (stamp duty, mortgage, commute annualisation, total monthly cost, etc.). These formulas are in `formulas.py` as raw strings — untestable Python, invisible to the type system, no way to reason about dependencies.
- **The sheet is a presentation layer masquerading as a database**. It couples data storage, computation, and visual layout into one fragile thing.
- **The UI is a spreadsheet**. For non-technical users, a card/dashboard layout with grouped information and drill-down would be vastly easier to parse than a dense grid of 40+ columns.

There is no way to build the web app without first extracting the derivation logic from the sheet formulas into Python. The DAG (directed acyclic graph) model is the cleanest way to do that.

## Solution

Two phases:

### Phase 1 — DAG Node Model (extract logic from sheet)

Define a node registry that encodes every value and its derivation. The registry knows:

- **What** each value is (schema, type, format)
- **Where** it comes from (which API or manual input)
- **How** it's computed (formula as a Python function)
- **What** it feeds into (dependencies → topological ordering)
- **How** it groups visually (zone in the card layout)

The DAG replaces the sheet formulas as the single source of truth for derivation. The sheet becomes a read-only archive.

### Phase 2 — Web UI (card-based dashboard)

Build a self-contained web app that reads enriched data (via the DAG) and renders it as a card-based property dashboard. The UI groups information into zones (Key Info, Commute & Area, Schools, Affordability, User Inputs) with summary indicators and expand-to-detail sections.

A UX design phase comes first — mock up the card layout, information hierarchy, and drill-down interaction before writing frontend code. The target user is non-technical; the UI must be glanceable.

---

## Node Model

### Node types

| Type | Description | Examples |
|------|-------------|---------|
| `source` | Raw input — API result or manual entry | EPC floor area, commute time, price, bedrooms |
| `derived` | Computed from other nodes | Stamp duty, monthly mortgage, total monthly cost |
| `group` | Logical container for display | "Key Info", "Affordability", "Commute & Area" |

### Node definition

```python
@dataclass
class ValueNode:
    """A single value in the property model.

    id             — unique key (e.g. ``stamp_duty``, ``simon_commute_time``)
    label          — human-readable name (e.g. "Stamp Duty")
    description    — what this value means
    kind           — source | derived | group
    deps           — list of node IDs this node depends on
    compute        — callable(*deps) → value (only for derived nodes)
    value_type     — int | float | str | Money | ...
    display        — display hint (currency, duration, percentage, text)
    group_id       — which group node this belongs to
    enrich_module  — for source nodes, which enrichment module produces it
    sheet_column   — for source nodes, the sheet column header (backward ref)
"""
```

### Node registry location

`houses/model/nodes.py` — single module that declares every node. This is the canonical list of every value the system knows about.

The registry is just a `dict[str, ValueNode]` with a helper that builds it by scanning declared nodes. Each node is defined declaratively so an agent or human can add one by adding a single entry.

### Example nodes

```python
NODES = {
    "price": ValueNode(
        id="price",
        label="Purchase Price",
        kind="source",
        value_type=float,
        display="currency",
        group_id="key_info",
    ),
    "stamp_duty": ValueNode(
        id="stamp_duty",
        label="Stamp Duty",
        kind="derived",
        deps=["price", "status"],
        compute=lambda price, status: 0 if status == "Current" else _calc_stamp_duty(price),
        value_type=float,
        display="currency",
        group_id="affordability",
    ),
    "total_monthly_housing": ValueNode(
        id="total_monthly_housing",
        label="Total Monthly Housing Cost",
        kind="derived",
        deps=["mortgage_payment", "sinking_fund", "life_insurance", "commute_cost", "council_tax", "status"],
        compute=lambda m, sf, li, cc, ct, s: _total_housing(m, sf, li, cc, ct, s),
        value_type=float,
        display="currency",
        group_id="affordability",
    ),
}
```

### Data flow

```
Enrichment APIs ──→ source nodes ──→ derived nodes ──→ group nodes
                                        ↑                  ↓
                                Sheet formulas          Web UI
                                (ported to Python)   (reads DAG output)
```

For any property, the system:
1. Runs the enrichment pipeline (unchanged — it produces the source values)
2. Feeds source values into the DAG
3. Topologically sorts and computes all derived nodes
4. Returns a flat dict of all computed values
5. The web UI renders them grouped by `group_id`

---

## Phase 1 — DAG Node Model (estimated: 1.5–2 weeks)

### Stage 1.1 — Port sheet formulas to Python

Ported formulas (all the logic currently in `formulas.py` sheet strings):

| Formula | Source | Dependencies |
|---------|--------|-------------|
| Stamp duty | `DATA_FORMULA_COLS["stamp duty (£)"]` | price, status |
| Net Ashby contribution | `DATA_FORMULA_COLS["net ashby contribution (£)"]` | price, stamp_duty, ashby_works_estimate, status |
| Mortgage required | `DATA_FORMULA_COLS["mortgage required (£)"]` | price, deposit, net_ashby_contribution |
| Monthly mortgage payment | `DATA_FORMULA_COLS["monthly mortgage payment (£)"]` | mortgage_required, rate, term |
| Yearly sinking fund | `DATA_FORMULA_COLS["yearly sinking fund (£)"]` | price, sinking_fund_rate |
| Best latitude | `DATA_FORMULA_COLS["best latitude"]` | actual_latitude, approx_latitude |
| Best longitude | `DATA_FORMULA_COLS["best longitude"]` | actual_longitude, approx_longitude |
| Map URL | `DATA_FORMULA_COLS["map url"]` | best_latitude, best_longitude |
| Monthly sinking fund (View) | `VIEW_FORMULA_COLS["monthly sinking fund (£)"]` | yearly_sinking_fund |
| Monthly commute cost (View) | `VIEW_FORMULA_COLS["monthly commute cost (£)"]` | bracknell_cost, simon_cost, lorena_cost |
| Monthly council tax (View) | `VIEW_FORMULA_COLS["monthly council tax (£)"]` | council_tax_yearly |
| Total monthly housing cost (View) | `VIEW_FORMULA_COLS["total monthly housing cost (£)"]` | mortgage, sinking_fund, life_insurance, commute_cost, council_tax, status |

Each formula becomes a pure Python function in `houses/model/formulas.py` with unit tests that match the sheet's behaviour.

### Stage 1.2 — Build the node registry and resolver

- Define `ValueNode` dataclass
- Build `NODES` dict in `houses/model/nodes.py`
- Build `resolve(property_data: dict, nodes: dict[str, ValueNode]) -> dict[str, Any]`
  - Topological sort
  - Walk the DAG, computing derived nodes in order
  - Return flat dict of all node values keyed by `id`
- Handle missing inputs gracefully (skip downstream derived nodes, report gaps)

### Stage 1.3 — Wire into the enrichment pipeline

After `run_enrichment()` produces `EnrichedProperty`, pipe it through the DAG resolver to get all derived values. This becomes the canonical output format.

The sheet writer becomes a downstream consumer of the DAG output (same as the web UI will be). Replace the `Row.from_property()` formatting with a DAG-to-sheet-row adapter.

### Stage 1.4 — Port conditional formatting rules

The sheet's conditional formatting rules (colour-coding EPC, commute times, Ofsted ratings, grey-out on "No" status) encode domain knowledge about what's good or bad. These should become named thresholds or scoring functions on the relevant nodes, not presentation-layer formatting.

Each node gets an optional `rating` function: `rating(value) → "good" | "warn" | "bad" | None`. The web UI uses this to colour indicators.

---

## Phase 2 — Web UI (estimated: 2–3 weeks)

### Stage 2.1 — UX design

Before writing any frontend code, design the card layout. This should produce:

- **Wireframes** for the property overview card showing all zones
- **Information hierarchy** — what goes in the summary vs what's behind a drill-down
- **Interaction model** — how to navigate between properties, how drill-down works
- **Scoring/glanceability** — how colour indicators work
- **Responsive breakpoints** — desktop first, but usable on mobile

Design artefacts should be HTML mockups (as is the user's convention — see the chronic-wellness `design/` directory) or a lightweight Figma.

Key design questions to resolve:
- One property per page, or a list with expandable cards?
- How do the 5 sheet zones translate to card sections?
- Which values are headline indicators vs supporting detail?
- How do manual inputs (status, notes, Ashby works) get edited?
- How does a new property get added? (Keep the browser extension flow?)

### Stage 2.2 — Backend: serve DAG output via HTTP

The existing FastAPI server already has `GET /properties` and `POST /properties`. Add:

- `GET /properties/{rid}/card` — returns the full DAG-computed output for one property, grouped by `group_id`, with rating/colour metadata
- `GET /properties/{rid}/card/graph` — returns the node graph for this property (useful for debug/transparency)

The existing sheet sync is untouched — new rows still get written to the sheet, but the web UI reads from the DAG, not the sheet.

### Stage 2.3 — Frontend: card dashboard

Build a standalone frontend (this plan assumes server-rendered HTML via Jinja or HTMX to avoid adding a JS build step, but the UX design stage may recommend otherwise).

Pages:
- `/` — list of properties with summary cards
- `/properties/{rid}` — full property card with all zones

Each property card renders the 5 zones:

**Key Info** — price, bedrooms, EPC rating (coloured), floor area, age band, council tax, map link
**Commute & Area** — Simon/Lorena/Bracknell time + cost, route descriptions, walk to town
**Schools** — primary + secondary with Ofsted ratings (coloured), distances, commute
**Affordability** — mortgage, stamp duty, sinking fund, commute costs, council tax, total monthly cost
**User Inputs** — Ashby Works estimate, status, group notes, design/planning flags

Each zone is expandable/collapsible. Headline values show colour indicators.

### Stage 2.4 — Manual input editing

The web UI needs write-back for user-owned fields (status, notes, Ashby works, actual coordinates, etc.). Add `PATCH /properties/{rid}` that updates the sheet's user columns.

---

## Sheet migration

The sheet stays as a readable archive. After the DAG is live:

- **Data tab**: still written by enrichment, but the web UI never reads from it directly
- **View tab**: still updated by formula sync, but becomes a legacy view
- **No backfill required** — existing data remains where it is
- New properties are enriched and written to the sheet as before, and also served via the DAG

---

## Adding a module in the new model

1. Add `source` node to `NODES` (or add fields to an existing enrichment module)
2. Write the enrichment function (unchanged from current pattern)
3. Wire it into `run_enrichment()`
4. If there are derived values, add `derived` nodes with `compute` functions
5. Assign to a `group_id` for UI placement
6. Done — no sheet columns, no formula sync, no view migration

---

## Risks

| Risk | Mitigation |
|------|------------|
| **Formula porting mis-matches** — derived values differ from sheet | Write exhaustive unit tests comparing Python output against sheet formula results for real properties. Use the sheet's existing data as a test oracle. |
| **DAG is over-engineered for the current module count** (~8 modules) | Could just port formulas to plain functions without the full DAG machinery. But the DAG cost is modest (one dataclass + a toposort) and pays for itself the first time you add a module. |
| **UX design scope creep** | Limit the first UX pass to the card layout + drill-down. Skip comparison views, graphs, dashboards across properties in V1. |
| **Loss of sheet interactivity** (conditional formatting, hyperlinks, real-time editing) | The sheet stays writable and readable. The web UI is additive — it doesn't block sheet access. |
| **Two sources of truth during migration** | Brief overlap only. The DAG output and sheet output must match for every property. Use the `/properties/compare` endpoint pattern to validate. |

---

## Not in scope (V1)

- Property comparison views (side-by-side)
- Cross-property dashboards or charts
- Automated "Ashby works" scoring
- Mobile app
- Real-time updates (poll or push)
- The dependency graph *display* — the DAG is a computational model, not a visualisation

---

## Decision points

Before starting Phase 2, decide:

- **Frontend stack**: server-rendered HTML (Jinja/HTMX) vs SPA (React/Svelte/Vue)? The UX design may influence this.
- **Data source**: does the web UI read from the sheet (existing `reader.py`) or from a new API endpoint? Recommendation: new endpoint that returns DAG output.
- **Write-back**: how do user edits (status, notes) flow back to the sheet? Simplest: direct sheet write via existing `write_enriched_row` machinery.
- **Deployment**: how is the web UI served? Same FastAPI process? Separate? Same process during development, likely.
