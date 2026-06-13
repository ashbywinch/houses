# Unify Transit Pipeline

## Problem

`_google_transit_commute` tries to replicate TfL's job (full transit routing
with pricing) via Google Routes API, but Google returns no UK transit fare
data.  This makes Google-first ordering in `get_commute` an unnecessary API
call for London routes, and the function itself is redundant.

## Solution

Restore TfL-first ordering, remove `_google_transit_commute`, and extract
parsers that produce fully normalised `list[JourneyLeg]` (always with
`start_station`/`end_station` set).

### Pipeline

```
TfL API  ───→ _parse_tfl_legs() ──→ list[JourneyLeg] ──→ CostGroups (in TransitRoute.plan)
Google API ─→ _parse_google_steps()                         ↓
  (bus alt only)                                             ↓
                                  ┌──────────────────────────┘
                                  ↓
                          _enrich_rail_fares
                          (leg-aware CRS lookup
                           using station names
                           from JourneyLeg)
```

### Guarantee

Every `JourneyLeg` produced by either parser always has `start_station`
and `end_station` set to the stop/station name from the API response
(empty string only when the API genuinely doesn't provide one, like a
bare walk step with no destination).

## Stages

### Stage 1 — TfL-first ordering, remove `_google_transit_commute`

Flip `get_commute()` to TfL-first.  Delete `_google_transit_commute` and
`_CostGroupBuilder` (no longer needed).  Keep `_find_bus_alternative` /
`_walk_to_station_minutes` / walking API — those are genuine augmentations.

Remove the `allow_bus` parameter from `_tfl_transit_commute` (Google was
the only caller that set it).

### Stage 2 — Extract `_parse_tfl_legs()`

Pull leg parsing out of `_build_cost_groups` into a pure function that
returns `list[JourneyLeg]`.  Every leg gets `start_station`, `end_station`,
`line_name`, `mode`, `duration_minutes`.

### Stage 3 — Extract `_parse_google_steps()`

Same pattern for the Google bus-alternative path (`_find_bus_alternative`).
Pull step parsing into a pure function returning `list[JourneyLeg]`.

### Stage 4 — Leg-aware NR fare fallback

Replace `_enrich_rail_fares` nearest-station heuristic with CRS lookup
using actual station names from the parsed `list[JourneyLeg]`.

### Stage 5 — Unify bus fare coordinate extraction

Shared `_normalize_stop_coords()` helper for both API formats.
