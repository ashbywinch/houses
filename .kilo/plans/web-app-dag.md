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

## Persistence (provenance must survive restarts)

The `NodeResult` cache **must be persistent**. The debugging scenario depends on it — the user reports a bug hours later, the agent queries the graph endpoint, and the enrichment-time state is still there.

Storage: SQLite, single table:

```sql
CREATE TABLE node_results (
    property_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    result_json TEXT NOT NULL,     -- serialized NodeResult
    updated_at TEXT NOT NULL,      -- ISO 8601
    PRIMARY KEY (property_id, node_id)
);
```

On enrichment completion, upsert all node results. On manual input change, invalidate+recompute the affected subtree and upsert those nodes. The graph endpoint reads directly from this table (or from an in-memory cache that's a read-through to SQLite).

This means:
- Server restart → data survives
- Bug report hours/days later → agent queries graph, gets exact enrichment-time state
- Multiple properties → each has its own independent graph
- Re-enrichment → overwrites node results, previous state is replaced

## Why not a graph DB?

This IS a graph — directed, node-attributed, with parent references. Graph DBs exist for exactly this pattern. But the complexity doesn't pay at our scale:

**What a graph DB adds:** separate server process, query language (Cypher/DQL), connection pool, backup strategy, schema management. That's ~500 lines of ops code before one query.

**What we actually need:** load all node results for a property (keyed by node_id), then walk the `deps` tree in memory via dict lookups. For 40 nodes × 100 properties = 4000 rows, this takes ~2ms.

### Two approaches that avoid a graph DB

**A. Store deps as a JSON list in the NodeResult.** `dep_ids: list[str]` instead of recursive `deps: dict[str, NodeResult]`. Query all rows for a property, build the tree in Python:

```python
rows = db.execute("SELECT node_id, result_json FROM node_results WHERE property_id = ?", (pid,))
results = {row[0]: NodeResult.from_json(row[1]) for row in rows}

def subtree(nid: str, depth: int) -> dict:
    result = results[nid]
    if depth > 0:
        result = replace(result, deps={
            dep: subtree(dep, depth - 1) for dep in result.dep_ids if dep in results
        })
    return result
```

**B. Same flat table, use SQLite recursive CTE for traversal.** No recursive Python — the traversal happens in the database:

```sql
WITH RECURSIVE dep_tree(node_id) AS (
    SELECT 'stamp_duty'
    UNION
    SELECT value FROM node_results, json_each(node_results.dep_ids)
    WHERE node_results.property_id = ? AND node_results.node_id = dep_tree.node_id
)
SELECT * FROM node_results WHERE property_id = ? AND node_id IN dep_tree;
```

Either way: flat SQLite table, no graph DB server, no new query language, no migration tooling. The "graph" part is handled by Python dict lookups or a `WITH RECURSIVE` query — both trivial at our data volume.

## What happens when the DAG changes in code

The `NODES` dict is code. When it changes (new node, removed node, changed compute function, changed dependencies), the SQLite cache becomes stale. How to handle it depends on the change:

| Change | Effect on cache | Handling |
|--------|----------------|----------|
| **New node added** | No row for it in SQLite | `resolve()` skips cache for unknown nodes — computed fresh on next call. No migration needed. |
| **Node removed from NODES** | Orphan row in SQLite (references a node_id that no longer exists in `NODES`) | Harmless. `resolve()` ignores it. Could clean up with a one-shot script, or leave it — 40 bytes per property. |
| **Compute function changed** (e.g. stamp duty brackets updated) | SQLite still has the old result. The resolver doesn't know the function changed. | Simplest: re-enrich the property. The user already does this when they want fresh data. Alternatively: add a `version` field to `NodeDef`, hash it into the cache key, or store a DAG version in SQLite and invalidate on mismatch. |
| **Dependency changed** (e.g. `stamp_duty` now also depends on `is_first_time_buyer`) | Old cache has no `is_first_time_buyer` dep. Resolver sees missing deps → marks node as stale → recomputes. Handled automatically. |

**For V1, the simplest approach:** don't worry about cache invalidation on code changes. When the user changes code AND wants to see updated values for existing properties, they re-enrich (which they already do today). The SQLite rows get overwritten with fresh results. No migration tooling needed.

If stale data becomes a real problem later, add a DAG version constant:

```python
DAG_VERSION = 2  # bump when compute functions change
```

Store it in a `dag_meta` table. On server start, if the version doesn't match, truncate all `node_results`. Simple, no migration scripts needed.

---

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

## Retrospective debugging — the real agent use case

**The scenario:** The user reports "the total monthly cost on property X looks wrong." The agent has no idea when the user was looking. The enrichment might have taken a fallback path because an API was out of credits. The agent can't reproduce it because today that API works fine, or the user won't re-trigger enrichment on a property they already corrected.

**Logs don't help.** The agent doesn't know the timestamp. Even if they did, the relevant information (which API path was taken, what the fallback was, what intermediate values were) is spread across multiple log lines in a noisy stream.

**The graph endpoint solves this because provenance is captured at compute time and persisted.** The agent doesn't reconstruct what happened — they read what *did* happen:

```
User: "The total monthly cost on 123 Rightmove is wrong."

Agent:
→ GET /properties/{rid}/graph?node=total_monthly_housing&depth=3
→ Returns:
  total_monthly_housing: {
    value: 4500,
    deps: {
      commute_cost: {
        value: 320,
        deps: {
          simon_cost: {
            value: 0,
            source: "nr_fare_fallback",
            source_time: "2026-06-13T15:30:00Z",
            compute_info: {
              path: "TfL returned 402 (out of credits) → NR fare fallback"
            }
          },
          lorena_cost: { ... }
        }
      },
      mortgage_payment: { value: 2800, ... },
      council_tax: { value: 200, ... }
    }
  }

Agent: "Simon's commute cost is 0 because the TfL API was out of credits
when this property was enriched. The fallback produced no result for
Simon's station. That's the root cause."
```

The agent **did not need to know** when the enrichment happened. The `source_time` on each `NodeResult` tells them. The **fallback path** is captured in `compute_info.path`. The **error** is in the `status`/`error` fields. Everything they need was captured when the value was computed and stored alongside it.

**Key requirement: provenance must be persisted, not ephemeral.** The `NodeResult` cache must survive server restarts (SQLite). When the user reports a bug hours or days later, the agent hits the graph endpoint and gets the exact state at enrichment time.

**What the agent *can't* do anymore:**
- Ask "when did you look at it?"
- Try to re-run enrichment and hope it fails the same way
- Read 500 lines of logs to find the relevant entries
- Parse unstructured text to understand what fallback was taken

**What the agent *can* do:**
- `GET /properties/{rid}/graph?node=total_monthly_housing&depth=10`
- Walk the JSON tree from the symptom back to the root cause
- Read `source`, `source_time`, `compute_info`, `status`, `error` on every node
- Find the exact API call that failed and why

---

## Not in scope (V1)

- Property comparison views
- Cross-property dashboards
- Automated "Ashby works" scoring
- Mobile app
- Real-time updates
