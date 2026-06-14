# Web App — Property Dashboard

## Problem

The enrichment engine works, but:

**Adding a module requires touching 7+ files** (EnrichedProperty, Row.HEADERS, from_property, enrichment function, run_enrichment, View tab formulas, migrate-view). The ceremony discourages iteration.

**Provenance is impossible.** When a value is wrong, there's no way to trace "this stamp duty came from price £450k using the 5% bracket" back to "price came from Rightmove scrape at 10:30." The agent digs through unstructured logs and often can't figure out what happened.

**Caching is coarse.** Changing one input (lat/lng) should only recompute the affected subtree (commute, schools, map URL), not re-run everything. Currently it does.

**The UI is a 40-column spreadsheet** — hard for non-technical users to glance at.

## Solution

A home-rolled DAG resolver. ~150 lines total. Every value is a node with explicit dependencies. Every node result carries provenance metadata so agents and humans can trace from any displayed value back to its source.

No external DAG library. No Rust. Just a node registry, a topological sort, and a compute loop.

---

## What makes this easy to reason about

**The node registry is one file.** All ~40 nodes declared in a single dict, top to bottom. You can read `houses/model/nodes.py` and see every value the system knows about, what it depends on, and how it's computed.

**Compute functions are pure Python.** No decorators, no registration machinery, no meta-programming. A function like `_calc_stamp_duty(price, status) → float` is called directly by the resolver with its dependency values already resolved. Testable in isolation.

**The graph endpoint shows the whole chain.** For any displayed value, an agent can GET `?node=stamp_duty&depth=3` and get back a JSON tree showing that value, its dependencies recursively, and the status/source/intermediates of each. No log parsing, no guesswork.

---

## Core types

```python
@dataclass
class NodeDef:
    """Schema for one node in the computation graph."""
    id: str
    label: str
    kind: Literal["source", "derived", "manual"]
    deps: list[str] = []              # node IDs this depends on
    compute: Callable | None = None   # for derived nodes
    enrich_field: str | None = None   # for source nodes — which EnrichedProperty field
    display: Literal["currency", "duration", "percent", "text", "badge"] = "text"
    rating_fn: Callable | None = None # value → "good" | "warn" | "bad" | None
    group_id: str = ""
    description: str = ""             # what this value means, for the agent
```

```python
@dataclass
class NodeResult:
    """Result of computing one node for one property.

    Every field is optional — a node might not have a value yet (missing source),
    might have errored, or might be pending. The agent can inspect *any* field
    to understand what happened.
    """
    # The value
    value: Any = None

    # What happened
    status: Literal["ok", "missing", "error"] = "missing"
    error: str | None = None           # full traceback or message

    # Where it came from
    source: str = ""                   # "epc_api", "rightmove_scraper", "formula:stamp_duty"
    source_time: datetime | None = None
    source_status_code: int | None = None

    # How it was computed (for derived nodes)
    compute_info: dict | None = None   # formula name, input values, intermediate steps

    # Dependencies — recursive. This is what lets agents traverse the graph.
    deps: dict[str, "NodeResult"] = field(default_factory=dict)
```

---

## Node registry

Single file. Every node the system knows about.

```python
# houses/model/nodes.py

NODES: dict[str, NodeDef] = {}

def node(
    id: str, label: str, kind: str, *,
    deps: list[str] | None = None,
    compute: Callable | None = None,
    enrich_field: str | None = None,
    display: str = "text",
    rating_fn: Callable | None = None,
    group_id: str = "",
    description: str = "",
):
    NODES[id] = NodeDef(id=id, label=label, kind=kind, deps=deps or [],
                        compute=compute, enrich_field=enrich_field,
                        display=display, rating_fn=rating_fn,
                        group_id=group_id, description=description)

# ── Source nodes (from enrichment) ──

node("price", "Purchase Price", "source",
     enrich_field="price", display="currency", group_id="key_info",
     description="From Rightmove listing or manual entry")

node("epc_rating", "EPC Rating", "source",
     enrich_field="epc_rating", display="badge", group_id="key_info",
     rating_fn=_epc_colour,
     description="A-G band from the Energy Performance Certificate")

node("floor_area", "Floor Area (m²)", "source",
     enrich_field="floor_area", display="text", group_id="key_info",
     description="Total floor area from EPC. Not always present.")

node("simon_commute_time", "Simon Commute (min)", "source",
     enrich_field="simon_commute.duration_minutes", display="duration",
     group_id="commute",
     description="Door-to-door transit time from property to Simon's office")

# ... all ~25 source nodes

# ── Derived nodes (computed from source nodes) ──

node("stamp_duty", "Stamp Duty", "derived",
     deps=["price", "status"],
     compute=lambda price, status: 0 if status == "Current" else _calc_stamp_duty(price),
     display="currency", group_id="affordability",
     description="UK stamp duty based on purchase price. Zero for current properties.")

node("total_monthly_housing", "Total Monthly Housing Cost", "derived",
     deps=["mortgage_payment", "sinking_fund", "commute_cost", "council_tax", "status"],
     compute=_total_housing_cost,
     display="currency", group_id="affordability",
     description="Mortgage + sinking fund + commute + council tax + insurance. "
                 "Subtracts rental income for 'Current' properties.")

# ... all ~15 derived nodes

# ── Manual input nodes ──

node("status", "Status", "manual",
     display="badge", group_id="user_inputs",
     description="Current, For Sale, Offer Accepted, No, etc.")

node("ashby_works_estimate", "Ashby Works Estimate (£)", "manual",
     display="currency", group_id="user_inputs",
     description="Estimated contribution from Ashby Works toward this property")

# ... manual nodes
```

---

## Resolver (~90 lines)

```python
# houses/model/resolver.py

def resolve(
    enriched: EnrichedProperty,
    manual_inputs: dict[str, Any] | None = None,
    cache: dict[str, NodeResult] | None = None,
    invalidated: set[str] | None = None,
) -> dict[str, NodeResult]:
    """Compute all nodes in dependency order, with caching and provenance.

    Args:
        enriched: the raw enrichment result (source values)
        manual_inputs: user-supplied overrides (status, notes, coordinates, etc.)
        cache: previous NodeResults, keyed by node_id
        invalidated: set of node_ids that need recomputation (dirty set)

    Returns:
        dict of node_id → NodeResult for all nodes
    """
    cache = {} if cache is None else dict(cache)
    results: dict[str, NodeResult] = {}

    # Topological sort (Kahn's algorithm)
    in_degree = {nid: 0 for nid in NODES}
    for nid, ndef in NODES.items():
        for dep in ndef.deps:
            in_degree[nid] += 1

    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    order = []
    while queue:
        nid = queue.pop(0)
        order.append(nid)
        for nid2, ndef in NODES.items():
            if nid in ndef.deps:
                in_degree[nid2] -= 1
                if in_degree[nid2] == 0:
                    queue.append(nid2)

    # Compute in topological order
    for nid in order:
        ndef = NODES[nid]
        needs_recompute = (invalidated and nid in invalidated) or nid not in cache

        if not needs_recompute:
            # Check if any dependency was recomputed
            for dep in ndef.deps:
                if dep in results or (invalidated and dep in invalidated):
                    needs_recompute = True
                    break

        if needs_recompute:
            # Gather dependency results
            dep_results = {}
            for dep in ndef.deps:
                if dep in results:
                    dep_results[dep] = results[dep]
                elif dep in cache:
                    dep_results[dep] = cache[dep]

            # Check for missing deps
            missing_deps = [d for d in ndef.deps if d not in dep_results]
            if missing_deps:
                results[nid] = NodeResult(
                    status="missing",
                    error=f"missing dependencies: {missing_deps}",
                    deps=dep_results,
                )
                cache[nid] = results[nid]
                continue

            # Compute
            try:
                if ndef.kind == "source":
                    val = _extract_source(ndef, enriched)
                    results[nid] = NodeResult(
                        value=val,
                        status="ok" if val is not None else "missing",
                        source=ndef.enrich_field or ndef.id,
                        source_time=datetime.now(timezone.utc),
                        deps=dep_results,
                    )
                elif ndef.kind == "derived":
                    dep_values = {d: r.value for d, r in dep_results.items()}
                    result = ndef.compute(**dep_values)
                    results[nid] = NodeResult(
                        value=result,
                        status="ok",
                        source=f"formula:{ndef.id}",
                        source_time=datetime.now(timezone.utc),
                        compute_info={
                            "function": ndef.compute.__name__,
                            "inputs": {d: r.value for d, r in dep_results.items()},
                        },
                        deps=dep_results,
                    )
                elif ndef.kind == "manual":
                    val = manual_inputs.get(nid) if manual_inputs else None
                    results[nid] = NodeResult(
                        value=val,
                        status="ok" if val is not None else "missing",
                        source="manual_input",
                        source_time=datetime.now(timezone.utc),
                        deps=dep_results,
                    )
            except Exception as e:
                results[nid] = NodeResult(
                    status="error",
                    error=traceback.format_exc(),
                    deps=dep_results,
                )
        else:
            results[nid] = cache[nid]

        cache[nid] = results[nid]

    return results
```

This is ~90 lines. The provenance is built-in: every `NodeResult` carries its deps, source, timestamp, and compute_info. Agents don't need to reconstruct anything.

---

## Provenance endpoint

```
GET /properties/{rid}/graph
GET /properties/{rid}/graph?node=stamp_duty&depth=2
```

Returns the `NodeResult` tree. Without `node`, returns the full DAG structure (schema — what nodes exist, their types, dependencies, group assignments).

With `node` and `depth`, returns the result for that node and its dependencies recursively up to `depth`. The agent walks this tree to find root causes.

Example agent interaction:

```
> GET /properties/{rid}/graph?node=stamp_duty&depth=2
→ {value: 10000, status: "ok", deps: {
    price: {value: 450000, status: "ok", source: "rightmove_scraper", ...},
    status: {value: "For Sale", status: "ok", source: "manual_input", ...}
  }, compute_info: {function: "_calc_stamp_duty", inputs: {price: 450000, ...}}
}

> "why is stamp duty 0?"
→ agent checks node=stamp_duty, sees deps.status.value = "Current",
  knows the formula returns 0 for "Current". Root cause found.
```

For human readability, the endpoint also accepts `?format=mermaid` or `?format=tree` to return a text diagram instead of JSON.

---

## Caching

Simple dict cache, keyed by `property_id/node_id`. On enrichment completion, cache all node results. On manual input change (lat/lng, status), pass `invalidated={node_id}` to `resolve()` which recomputes only the affected subtree.

Cache storage: in-memory for development, SQLite for persistence (a single table: `property_id, node_id, result_json, updated_at`).

---

## Phases

### Phase 1 — DAG foundation (estimated: 1–1.5 weeks)

| Stage | What | Files |
|-------|------|-------|
| 1.1 | Core types: `NodeDef`, `NodeResult` | `houses/model/__init__.py` |
| 1.2 | Port sheet formulas to pure Python functions + unit tests | `houses/model/formulas.py` |
| 1.3 | Build node registry | `houses/model/nodes.py` |
| 1.4 | Build resolver + cache | `houses/model/resolver.py` |
| 1.5 | Wire into enrichment pipeline: `run_enrichment()` → `resolve()` → cache | `houses/enrichment_runner.py` (minimal changes) |
| 1.6 | Provenance endpoint `GET /properties/{rid}/graph` | `houses/server.py` |

### Phase 2 — UX design (estimated: 0.5–1 week)

Wireframes for the card dashboard:
- 5 zones: Key Info, Commute & Area, Schools, Affordability, User Inputs
- Headline values with colour indicators
- Drill-down provenance: click a value → see its dependency chain
- Error states: missing values, failed API calls, stale data
- How manual inputs get edited

Design artefacts: HTML mockups, following the chronic-wellness `design/` convention.

### Phase 3 — Web UI (estimated: 2–3 weeks)

- `GET /properties/{rid}/card` — grouped node results with rating colours and optional provenance
- Frontend: property list + detail card with expandable provenance
- Manual input editing (PATCH → sheet or cache)
- Validation: compare DAG output against sheet for all existing properties

---

## Adding a module in the new model

1. Add `node(...)` entry to `houses/model/nodes.py` for each new value
2. Write the enrichment function (same as today)
3. Wire into `run_enrichment()` (same as today)
4. If there are derived values from the new data, add derived nodes with compute functions
5. Done — no sheet columns, no formula sync, no view migration

---

## How this helps agents debug

**Before:** "The stamp duty is wrong. Let me read the logs to see what price was used. Actually let me try running the server locally. The logs are noisy. I can't find the right line."

**After:**
```
GET /properties/{rid}/graph?node=stamp_duty&depth=2
→ sees price=450000, status="For Sale"
→ formula: _calc_stamp_duty(450000, "For Sale")
→ result: 10000

# If something is missing:
GET /properties/{rid}/graph?node=simon_commute_time
→ status: "missing"
→ error: "TfL API returned 429 at 2026-06-14T10:30:00Z"
→ source_time: 2026-06-14T10:30:00Z

# If an error:
GET /properties/{rid}/graph?node=epc_rating
→ status: "error"
→ error: "Traceback: ... KeyError: 'currentEnergyEfficiencyBand'"
→ source: "epc_api"
```

No log parsing. No server restart. One HTTP call per node, walking the dependency tree.

---

## Not in scope (V1)

- Property comparison views
- Cross-property dashboards
- Automated "Ashby works" scoring
- Mobile app
- Real-time updates
