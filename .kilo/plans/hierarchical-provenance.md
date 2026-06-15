# Plan: Hierarchical Provenance

## Key Insight

The provenance tree is **already persisted in the DAG** — `dep_versions` encodes
exactly which source rows each derived value consumed. A DFS walker can reconstruct
the full tree at render time. No new storage, no provenance-passing through compute
functions, no enrichment-module changes.

What's missing is a query tool and a rich display component.

## Corrections: What the Tree Should Actually Show

### 1. Leaf nodes show the actual value, not just the source name

```
"31 Isambard Road, Southall UB2 4GN (User correction)"
                                                ↕ source
                                           actual value
```

Not `"User correction"` — that tells you where it came from but not *what it is*.
Every leaf in the tree is a DAG source or user_input node whose value is known.

### 2. Hyperlink data sources

Data sources with a URL get a link:

```
BODS fares ↗   →  https://data.bus-data.dft.gov.uk/
TfL API ↗      →  https://api.tfl.gov.uk/
```

The URL is declared alongside the node or on the configuration entry.

### 3. Computation / transformation nodes

A derived node's compute function is itself a step in the provenance. The tree
should show what operation was performed:

```
Replace long walk leg with bus
├── walk_to_station  12 min  (from TfL route)
├── bus_time         13 min  (from Google Routes)
├── savings           9 min  (walk - bus - penalty)
└── threshold        10 min  (config: bus_walk_penalty_minutes)
```

These aren't DAG nodes — they're intermediate values produced *during* the
computation. The compute function can optionally declare them.

### 4. Route dependencies include origin and destination

TfL API didn't just produce a commute — it produced a commute *from address A
to address B*. Those inputs are themselves DAG nodes:

```
TfL API
├── origin      31 Isambard Road, Southall UB2 4GN (User correction)
├── destination EC3A 7LP (Default)
└── result      32 min, £4.50 (TfL)
```

### 5. Configuration values are dependencies

`max_walk_minutes`, `bus_walk_penalty_minutes`, `petrol_price_per_litre` all
affect the result. They should appear in the tree as configuration nodes.

## Full Example: Lorena Commute with Bus Replacement

```
best_lorena_commute  "32 min, £4.50" (TfL + Bus)
│
├── computation  "Replace long walk leg with bus"
│   ├── walk_to_station      12 min  (from TfL route)
│   ├── walk_threshold       10 min  (config: bus_walk_penalty_minutes)
│   ├── walk_exceeds?        yes     (12 >= 10)
│   ├── bus_time              8 min  (from Google Routes)
│   ├── bus_fare             £1.75
│   ├── time_savings          4 min  (12 - 8)
│   ├── savings_exceeds?     no      (4 < 10)  ← why replacement was REJECTED
│   └── decision             "Keep TfL route as-is"
│
├── lorena_tfl_route  "32 min, £4.50 TfL"
│   ├── origin     "31 Isambard Road, Southall UB2 4GN (User correction)"
│   │   └── rightmove_address  "Pembroke Avenue, Hersham, KT12 (Rightmove)"
│   ├── destination  "EC3A 7LP (Default)"
│   └── TfL API ↗
│
├── lorena_bus_alternative  "8 min, £1.75"
│   ├── origin     "31 Isambard Road, Southall UB2 4GN (User correction)"
│   ├── destination  "EC3A 7LP (Default)"
│   └── Google Routes API ↗
│       └── fare  "LONDON UNITED RT1 → RT2 (fuzzy match)"
│           └── BODS fares ↗  (https://data.bus-data.dft.gov.uk/)
│
└── best_address  "31 Isambard Road, Southall UB2 4GN (User correction)"
    └── rightmove_address  "Pembroke Avenue, Hersham, KT12 (Rightmove)"
```

## Design

### 1. ProvenanceNode

```python
@dataclass
class ProvenanceNode:
    node_id: str                # DAG node id, or "__computation__", "__config__"
    label: str                  # display label (may include value)
    url: str = ""               # hyperlink for data source
    value: str = ""             # the actual value (shown in parentheses)
    children: list["ProvenanceNode"] = field(default_factory=list)
```

### 2. Base DFS walker (zero domain knowledge)

Walks `dep_versions` links, produces tree from `PropertyData` alone:

```python
def provenance_tree(data: PropertyData, node_id: str) -> ProvenanceNode | None:
    derived = data.derived.get(node_id)
    if derived:
        children = []
        for dep_id in derived.dep_versions:
            child = provenance_tree(data, dep_id)
            if child:
                children.append(child)
        return ProvenanceNode(
            node_id=node_id,
            label=_format_label(node_id, derived),
            children=children,
        )

    source = data.sources.get(node_id)
    if source:
        return ProvenanceNode(
            node_id=node_id,
            label=_format_value(source.value, source.source),
        )
    # similarly for user_input
    return None
```

`_format_label` and `_format_value` are simple helpers that produce
"value (Source)" strings.

### 3. Computation node injection (optional, domain-specific)

For derived nodes whose compute function has interesting intermediate steps,
a `_provenance_detail(node_id, dep_values, config) -> list[ProvenanceNode]`
function can inject computation nodes. This is domain-specific and lives
alongside the relevant enrichment module, not in the DFS walker.

For example, the `_replace_walk_with_bus` logic would have a detail function
that reads the relevant config values, computes intermediate values, and
returns child nodes describing the decision.

The DFS walker checks for a registered detail function for the current
`node_id` and, if one exists, appends its return to the children.

### 4. Data source URLs

Declared on `NodeDef` as an optional field:

```python
node(id="lorena_bus_fare", kind=NodeKind.source,
     source_url="https://data.bus-data.dft.gov.uk/")
```

The DFS walker reads `source_url` and sets `ProvenanceNode.url`.

### 5. Configuration dependency marking

Configuration keys that a compute function depends on are declared in a
decorator or a module-level dict:

```python
_REPLACE_WALK_DEPS = {
    "bus_walk_penalty_minutes": ("Config", "bus_walk_penalty_minutes"),
    "max_walk_minutes": ("Config", "max_walk_minutes"),
}
```

The detail function reads these, looks up the current values from `settings`,
and injects config nodes:

```python
ProvenanceNode(
    node_id="__config__",
    label=f"bus_walk_penalty_minutes = {settings.bus_walk_penalty_minutes}",
)
```

## Implementation Steps

1. Add `ProvenanceNode` + `source_url` to `NodeDef`.
2. Write `provenance_tree()` — pure DFS in `model/resolver.py`.
3. Add optional detail-function registry for computation nodes.
4. Create `_provenance_tree.html` recursive partial, with hyperlink support.
5. Add a toggle button on the detail page for each derived node.
6. Migrate `_replace_walk_with_bus` as the first detail-function example.

The enrichment layer is untouched. Existing compute functions continue returning
`(value, source)` tuples as today. The tree adds richness over time as detail
functions are written for key computations.
