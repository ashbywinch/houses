# TransitRoute Class — Phase 2

## Domain Concept

A **`Commute`** is a regular journey between home and a destination.
All three commutes (Simon, Lorena, Bracknell) share the same result type.
Simon and Lorena use transit routing; Bracknell is driving. A commute
contains:

- **Duration** (minutes)
- **Cost** (daily round-trip GBP)
- **CostGroups**: groups of legs priced as a single unit, each with a
  cost and operator (TfL, Southern, parking company, etc.). One TfL
  tap-in covers tube→walk→tube as one CostGroup. Driving and parking
  are separate CostGroups. Together they power cost breakdowns,
  ticket-type recommendations, and human-readable summaries.
- **summary()**: a computed string rendering all legs for the sheet column.

A **`JourneyLeg`** is a single segment of a commute — walk 5 mins,
tube 4 mins, etc. Each leg knows its mode and duration only.
Descriptions come from the parent **`CostGroup`** which knows the
operator and can format leg descriptions appropriately (TfL names
lines, rail operators name services, etc.).

A **`TransitRoute`** is a public-transit itinerary between two places in
greater London. It wraps the TfL journey planner API, picks the best
route, then enriches it with park-and-ride, bus fares, and parking costs.

This is a noun — a Route. Not a "router" or "journey planner."

## `Commute` shape

```python
class LegMode(Enum):
    WALK = auto()
    TUBE = auto()
    BUS = auto()
    TRAIN = auto()
    DRIVE = auto()
    CYCLE = auto()
    PARK = auto()

class CommuteMode(Enum):
    TRANSIT = auto()
    DRIVE = auto()

@dataclass(frozen=True)
class JourneyLeg:
    """One segment of a commute journey."""
    mode: LegMode
    duration_minutes: int

@dataclass(frozen=True)
class CostGroup:
    """A contiguous set of legs priced as a single unit, by one operator.

    One TfL tap-in/tap-out covers tube→walk→tube as one CostGroup.
    One petrol stop covers the driving leg. One parking session has its
    own cost group and operator. Walking legs before and after transit
    are standalone (free, no operator).
    """
    legs: tuple[JourneyLeg, ...]
    operator: str = ""  # "TfL", "Southern", "Parking Co", etc.
    cost: Money | None = None  # None = free (walking)

    def leg_descriptions(self) -> tuple[str, ...]:
        """Return operator-appropriate descriptions for each leg.

        TfL might say "Northern line to Bank", a bus operator might
        say "Bus 63 towards King's Cross". Falls back to mode+duration
        for unknown operators.
        """
        ...

@dataclass(frozen=True)
class Commute:
    """A person's journey between home and a destination."""
    destination_label: str
    destination_postcode: str
    duration_minutes: int | None = None
    daily_cost_gbp: Money | None = None
    mode: CommuteMode = CommuteMode.TRANSIT

    # All legs in order, each inside a CostGroup. Boring CostGroups
    # (walking to/from transit) have no operator and no cost.
    cost_groups: tuple[CostGroup, ...] = ()

    def summary(self) -> str:
        """Render as the sheet's route-summary string."""
        parts: list[str] = []
        for group in self.cost_groups:
            for leg, desc in zip(group.legs, group.leg_descriptions()):
                parts.append(f"{desc} ({leg.duration_minutes}m)")
        return " \u2192 ".join(parts)
```

The existing `route_summary: str` becomes a computed property from
`cost_groups` → `legs`. Parking is now a `LegMode.PARK` leg within
a `CostGroup`. Bus cost lives on the `CostGroup` covering the bus leg.
No more separate `bus_cost_gbp` / `parking_cost_gbp` fields.

## Why a class, not functions

The current `compute_transit` is a 170-line god function with 7 mutable
local variables threaded through every branch. A class makes the state
explicit and each enrichment step is a clear method.

## API

```python
route = TransitRoute(
    origin="GU21 7QF",
    destination="SW1V 2QQ",
    label="Simon — Pimlico / Victoria",
    park_and_ride=True,
    allow_bus=False,
)
commute: Attempt[Commute] = await route.plan()
```

## Methods

| Method | Visibility | Returns | Responsibility |
|---|---|---|---|
| `plan()` | public | `Attempt[Commute]` | Plan the full route: fetch → pick → enrich → return |
| `_fetch_tfl()` | private | `dict \| None` | TfL API call + cache + 300 geocode fallback |
| `_enrich_park_and_ride(data)` | private | `dict` | Replace long walk with driving via ORS |
| `_pick_best(data)` | private | `(int, float, str)` | Pick shortest journey, extract details |
| `_add_bus_fare(commute)` | private | `Commute` | Look up bus leg costs |
| `_add_parking_cost(commute)` | private | `Commute` | Look up parking costs |

## All three commutes share `Commute` result

| Current function | Change | Returns |
|---|---|---|
| `compute_transit` | → `TransitRoute.plan()` | `Attempt[Commute]` |
| `compute_petrol_cost` | → `Attempt[Commute]` (same result type) | `Attempt[Commute]` |
| `compute_simon_commute` | thin wrapper | `Attempt[Commute]` |
| `compute_lorena_commute` | thin wrapper + keep Google fallback | `Attempt[Commute]` |

`compute_petrol_cost` currently returns `PetrolCost`. Changing it to
`Attempt[Commute]` means `compute_commute_breakdown` receives all three
commutes as `Commute` values — no special cases for petrol vs transit.

## File Structure

Current `models.py` is a structural name — it doesn't describe a domain concept.
The classes inside it belong to different domains:

| Current file | Current classes | Problem | New home |
|---|---|---|---|
| `models.py` | `PropertyPayload` | "Payload" is noise | `houses/property.py` as `Property` |
| `models.py` | `EnrichedProperty` | Fine | `houses/property.py` |
| `models.py` | `SchoolInfo`, `PetrolCost`, `CouncilTaxInfo` | All property attributes | `houses/property.py` |
| `models.py` | `TransitInfo` | Renamed to `Commute` | `houses/commute.py` |
| `models.py` | `CommuteBreakdown` | Summarises commutes | `houses/commute.py` |
| `models.py` | `ReprocessRequest` | API command, not property data | `houses/server.py` |

New file layout:

| File | Contains | Purpose |
|---|---|---|
| `houses/property.py` | `Property`, `EnrichedProperty`, `SchoolInfo`, `PetrolCost`, `CouncilTaxInfo` | Everything about a house |
| `houses/commute.py` | `Commute`, `JourneyLeg`, `CommuteBreakdown` | Commute value objects |
| `houses/transit_route.py` | `TransitRoute` | Transit route planning |
| `houses/server.py` | `ReprocessRequest` | Stays where it's used |

## Renames

| Old | New | Reason |
|---|---|---|
| `PropertyPayload` | `Property` | "Payload" is structural noise |
| `TransitInfo` | `Commute` | Domain noun |
| `PetrolCost` | removed → merged into `Commute` | `compute_petrol_cost` returns `Attempt[Commute]` |

## Phase Plan

### Phase A — Rename + extract files

1. Create `houses/property.py` with `Property`, `EnrichedProperty`, `SchoolInfo`, `PetrolCost`, `CouncilTaxInfo`
2. Create `houses/commute.py` with `Commute`, `JourneyLeg`, `CommuteBreakdown`
3. Rename `PropertyPayload` → `Property`, `TransitInfo` → `Commute`
4. Move `ReprocessRequest` into `houses/server.py`
5. Update all imports across the project

### Phase B — Convert value objects to frozen dataclasses

1. `Commute`, `JourneyLeg`, `CommuteBreakdown`, `SchoolInfo`, `PetrolCost`, `CouncilTaxInfo` → `@dataclass(frozen=True)`
2. Fix the in-place mutation in `_enrich_rail_fares` (server.py:808)
3. Fix `Commute.summary()` to derive from `legs`

### Phase C — Create `TransitRoute`

1. `houses/transit_route.py` with `TransitRoute.plan()` → `Attempt[Commute]`
2. Remove `compute_transit` from enricher.py
3. Thin wrappers `compute_simon_commute`, `compute_lorena_commute` stay
4. `compute_petrol_cost` → `Attempt[Commute]`

## Not In Scope

- `_compute_google_transit` stays in enricher.py (internal fallback)
- `_enrich_rail_fares` stays in server.py (separate concern)
- `_format_route_summary`, `_pick_best_journey` stay as helpers
