# Plan: Domain Class Abstraction for the DAG Model

## The Problem

The current DAG is composed of scalar leaf nodes (`rightmove_address`, `rightmove_bedrooms`,
`simon_commute`, `best_address`). Individual values are easy to store and track, but:

1. **Domain concepts are not front and centre.** A developer reading the code sees
   `simon_commute` as a node ID but doesn't immediately know it's a `Commute` value
   with `.minutes`, `.cost`, `.mode`, `.cost_groups`, etc.

2. **The naming is implementation-oriented.** `tfl_simon_raw` describes *where the data
   came from*, not *what the data is*. A name like `simon_commute` is better but still
   doesn't tell you what type it holds.

3. **Existing domain classes** (`Commute`, `Station`, `BusJourney`, `CostGroup`,
   `JourneyLeg`) are rich value objects with behaviours (distance calculations,
   fare lookups, cost aggregation) — but they're invisible to the DAG. The DAG only
   sees their JSON-serialised form.

## Current Approach: Scalar Leaf Nodes

```
simon_commute  →  Commute value (auto-serialised via TypeAdapter)
best_address   →  string
best_location  →  GeoPoint
rightmove_price →  string
```

**Pros:**
- Each field has independent staleness tracking. A price re-scrape doesn't touch address.
- Provenance per field: every value knows exactly where it came from.
- UI components can read individual scalars without unpacking a composite.
- Test setup is simple: `insert_source_value(rid, "rightmove_price", "250000", "Rightmove")`.

**Cons:**
- No grouping: a `Commute` object exists as a value blob in SQLite but the DAG's
  node declarations don't tell you it's a `Commute`. You have to read the caller.
- The `_deserialize_gp` helper for GeoPoint and the `_serialize_value` auto-detect
  handle flat dataclasses, but nested types (`Commute` containing `CostGroup` containing
  `JourneyLeg`) work through `TypeAdapter` which is invisible to the node definition.
- Adding a new field means writing a new node declaration, a new `insert_source_value`
  call, and a new template entry. It's mechanical but noisy.

## Proposed Approach: Domain-Typed Composite Nodes

Each domain concept becomes a single DAG node whose value is the full domain object:

```python
# One node for the whole commute, not separate nodes for minutes/cost/mode
node(id="simon_commute", kind=NodeKind.source,
     provenance_template="TfL")
```

The enrichment module writes a `Commute(duration=32, daily_cost=4.50, mode="transit",
cost_groups=(...))` to the DAG. The value auto-serialises to JSON with `_type`/`_module`
markers and deserialises back to a proper `Commute` object.

**Pros:**
- The type is in the code: `simon_commute` resolves to a `Commute` with `.minutes`,
  `.cost`, `.cost_groups`, etc.
- Domain behaviours stay on the class: `commute.non_rail_cost()`, `cost_group.operator`.
- Fewer nodes: one per domain concept instead of one per scalar.
- The template reads `simon_commute.value.minutes` directly — no manual field mapping.

**Cons:**
- Coarser granularity: if TfL re-scrape updates the journey but leaves the cost
  unchanged, the whole `Commute` is provably different. Staleness can't distinguish
  which field changed.
- Provenance per field is lost: the whole `Commute` gets one provenance badge
  ("TfL"). If the cost came from a blend of TfL and BODS fares and the duration
  came from Google Routes, you can't see that split at the DAG level.
- Composite is more work to test: you need a full `Commute` object in test setup,
  not just a scalar string.
- The `_replace_walk_with_bus()` function produces a *new* `Commute` that combines
  TfL data with bus data. If this becomes a derived node, the compute function
  needs to understand `Commute` internals — which it already does.

## Hybrid Recommendation

Use **composite domain nodes for data that arrives as a unit** (a `Commute` from
TfL, a `Station` from the station registry, a `BusJourney` from BODS). These are
always written by a single enrichment module and read as a whole.

Use **scalar leaf nodes for data that has multiple sources or needs per-field
provenance** (address, location, price, bedrooms — where user corrections,
Rightmove, and geocoding all contribute).

This means:

| Concept | Node type | Value type | Why |
|---------|-----------|------------|-----|
| Rightmove address | scalar `rightmove_address` | `str` | Multiple sources (user, Rightmove, import) |
| Rightmove price | scalar `rightmove_price` | `str` | Multiple sources |
| Rightmove location | scalar `rightmove_location` | `GeoPoint` | Overridden by user location |
| Simon commute | **composite** `simon_commute` | `Commute` | Single source (TfL), consumed as a unit |
| Lorena commute | **composite** `lorena_commute` | `Commute` | Single source (TfL), consumed as a unit |
| School | composite `nearest_school` | `School` or similar | Single source (GIAS + Ofsted) |
| Council tax | composite `council_tax_info` | `CouncilTaxInfo` | Single source (VOA) |

For derived nodes that combine sources:

| Derived | Dependencies | Value type | Reasoning |
|---------|-------------|------------|-----------|
| `best_address` | `corrected_address`, `rightmove_address` | `str` | Per-field provenance matters |
| `best_location` | `precise_location`, `rightmove_location`, `best_address` | `GeoPoint` | Per-field provenance matters |
| `best_simon_commute` | `simon_commute`, `simon_commute_rail`, `simon_walk` | `Commute` | Selects between whole `Commute` objects |
| `daily_commute_cost` | `simon_commute`, `simon_parking` | `Money` | Derived from commute + parking |

## Naming Convention

Nodes should be named as **domain nouns**, not implementation sources:

| Instead of | Use |
|------------|-----|
| `tfl_simon_raw` | `simon_commute` |
| `gias_primary` | `nearest_primary_school` |
| `voa_council_tax` | `council_tax` |

The `provenance_template` tells you *where* it came from. The node ID tells you
*what* it is.

## What This Means for Provenance

With scalar nodes, provenance is per-value and you can see "User correction" on
the address and "Rightmove map" on the coordinates. With composite nodes, the
whole object gets one provenance badge — "TfL" for the whole commute.

The per-field provenance within a composite is lost at the DAG level. If you need
it (e.g. commute cost came from TfL fares + BODS bus fares + car park data), you
need a richer provenance structure — see the separate hierarchical provenance plan.
