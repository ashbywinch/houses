# Hierarchical Provenance — Status

## Implemented

### Provenance dataclass (`dag/attempt.py`)

```python
@dataclass
class Provenance:
    label: str = ""
    description: str = ""
    value: Any = None
    url: str = ""           # hyperlink for data source
    sources: dict[str, Provenance] = field(default_factory=dict)
```

- `to_dict()` serialises all fields (url only when non-empty)
- `from_label(label, url="")` — leaf with optional URL
- `composite(label, sources, url="")` — composite with optional URL

### Recursive DFS via `build_provenance()` (`dag/derived_node.py`)

`DerivedNode.build_provenance()` walks active deps and gathers their provenance
recursively. Each dep's `build_provenance()` returns a `Provenance` with its
own sub-sources, producing a tree.

### `source_url` on Node base (`dag/node.py`)

`Node.__init__` accepts `source_url=""`. Included in `to_json()` output as
`source_url` when non-empty (separate from `Provenance.url`).

### Source URLs wired in domain nodes

| Node | URL |
|------|-----|
| EPCNode | https://www.epcregister.com/ |
| CouncilTaxNode | https://www.gov.uk/council-tax-bands |
| GeocodeNode | https://postcodes.io/ |
| SecondarySchoolNode | https://get-information-schools.service.gov.uk/ |
| WalkabilityNode | https://maps.googleapis.com/ |

### Vue frontend

- `Provenance` TS type includes `url?: string`
- `ProvenanceTree.vue` — recursive component rendering label, link, description, and child sources
- Wired into `PropertyDetail.vue` replacing flat `{{ c.provenance?.label }}` display

## Still Planned

### 1. Config dependency marking

Configuration keys that a compute function depends on should be declared in a
decorator or module-level dict:

```python
_REPLACE_WALK_DEPS = {
    "bus_walk_penalty_minutes": ("Config", "bus_walk_penalty_minutes"),
    "max_walk_minutes": ("Config", "max_walk_minutes"),
}
```

A detail function reads these, looks up current values from `settings`, and
injects config `Provenance` nodes.

### 2. Detail-function registry

For derived nodes whose compute function has interesting intermediate steps,
a `_provenance_detail(node_id, dep_values, config) -> list[Provenance]`
function can inject computation nodes. Lives alongside the relevant
enrichment module, not in the DFS walker.

Example: `_replace_walk_with_bus` logic would inject intermediate values:

```
Replace long walk leg with bus
├── walk_to_station  12 min  (from TfL route)
├── bus_time         13 min  (from Google Routes)
├── savings           9 min  (walk - bus - penalty)
└── threshold        10 min  (config: bus_walk_penalty_minutes)
```

### 3. Leaf value display

Leaf nodes should show the actual value, not just the source name:

```
"31 Isambard Road, Southall UB2 4GN (User correction)"
```

Not `"User correction"` — that tells you where it came from but not what it is.
Every leaf is a DAG source or user_input node whose value is known.

## Design Rationale (preserved for planned items)

### Config dependency marking

Configuration keys are declared explicitly near the compute function that uses
them, not in a central registry. This keeps the dependency declaration close
to the code that needs it.

### Detail-function registry

A module-level dict maps `node_id` → detail function. The DFS walker checks
for a registered function and, if one exists, appends its return to the
children. No enrichment-module changes needed — existing compute functions
continue returning their values as today.
