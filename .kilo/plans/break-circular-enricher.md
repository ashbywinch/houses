# Break the Circular Dependency: Extract Domain Classes from enricher.py

## Problem

`enricher.py` (966 lines) mixes 5 concerns, creating a circular dependency with
`routing.py` that manifests as inline imports of private symbols in 6 locations:

```
enricher.py                routing.py
  (inline)                   (inline)
──→ routing.get_commute     ──→ enricher._find_bus_alternative
──→ routing._with_label
──→ routing._drive_commute

commute.py
  (inline)
──→ enricher._shorten_station

transit_route.py
  (inline)
──→ enricher._shorten_station
```

Every inline import violates two rules: "never import private symbols from another
module" and "never import inside a function body."

## Domain Model

Three domain classes. Each bundles data with its behaviour.

### 1. `Station` — a railway/Tube station

Uses the existing `GeoPoint` for coordinates. The "short name" is a display
concern, not normalisation — we shorten station names for route summaries
("Paddington" not "Paddington Rail Station"), not to canonicalise them.

```python
# houses/stations.py

from dataclasses import dataclass
from houses.geo import GeoPoint


@dataclass
class Station:
    name: str        # canonical name from stations.csv
    crs: str         # e.g. "PAD"
    location: GeoPoint

    @staticmethod
    def short_name(raw: str) -> str:
        """Return a display-friendly station name.
        
        Strips ' Rail Station', ' Underground Station', ' London ' prefix
        so the name works in route summaries: 'walk to Paddington'.
        This is a display concern, not canonicalisation.
        """
        ...

    @property
    def short(self) -> str:
        """This station's short display name."""
        return Station.short_name(self.name)
```

The registry loads from `data/stations.csv` and caches the result:

```python
class StationRegistry:
    """Lazy-loaded singleton — loads stations.csv once, provides lookups.
    
    Uses instance state (not ``global``) for the cache.
    """
    _stations: dict[str, Station] | None = None
    _by_crs: dict[str, Station] | None = None
    
    def find(self, name: str) -> Station | None: ...
    def find_by_crs(self, crs: str) -> Station | None: ...

# Module-level convenience
_registry = StationRegistry()
find = _registry.find
find_by_crs = _registry.find_by_crs
```

| Source (enricher.py) | Target |
|---|---|
| `_shorten_station(name)` | `Station.short_name(raw)` (static) |
| `_clean_station_name_for_matching(name)` | absorbed into registry's matching |
| `_lookup_station_crs(station_name)` | `find(name).crs` |
| `_lookup_station_coords(station_name)` | `find(name).location` |
| `_STATION_SUFFIXES` | `Station.SUFFIXES` |

### 2. `BusJourney` — a bus journey with priced fare products

Not "BusFare" — a journey that happens to be by bus, with a menu of
available fare **products** (single, return, day ticket). The caller
picks the cheapest product for their journey context (weekday peak
return trip). Monetary values use `Money(amount, 'GBP')`.

**Data finding**: The JSON data confirms exactly 3 product types
(`adult_single`, `adult_return`, `adult_day`) and no zone pair ever
has more than one of each type. We encode this as a dict keyed by
type — the shape itself asserts the invariant.

```python
# houses/bus_journey.py

from dataclasses import dataclass, field
from money import Money
from enum import Enum, auto
from houses.geo import GeoPoint


class FareProductType(Enum):
    """The type of bus fare product available for a zone pair."""
    SINGLE = auto()    # adult_single
    RETURN = auto()    # adult_return
    DAY = auto()       # adult_day


@dataclass
class FareProduct:
    """A specific priced fare product between two zones.

    A zone pair has at most one product of each FareProductType
    (asserted at load time — see BusJourneyRegistry).
    """
    type: FareProductType
    price: Money  # always GBP
    operator: str
    zone_pair: str


@dataclass
class BusJourney:
    """A bus journey between two points, with available fare options.

    ``available_fares`` is a dict keyed by product type — at most one
    entry per key (asserted at load time). The caller looks up the
    product(s) that fit their journey pattern.
    """
    origin: GeoPoint
    destination: GeoPoint
    duration_minutes: int | None = None
    available_fares: dict[FareProductType, FareProduct] = field(default_factory=dict)
```

The registry loads `data/bus_fares.json` and exposes products for
a point-to-point lookup:

```python
class BusJourneyRegistry:
    """Lazy-loaded fare zone data — provides available fare products per route."""
    
    _data: dict | None = None
    _national_max_single: Money | None = None
    
    def fares_between(self, origin: GeoPoint, destination: GeoPoint) -> dict[FareProductType, FareProduct]:
        """Look up all available products between two points.
        
        Returns a dict with at most 3 keys (SINGLE, RETURN, DAY).
        Empty dict if no route found.
        ```
    
    @property
    def national_max_single(self) -> Money | None:
        """Government fare cap applied to single fares (from _meta)."""
        ...

    def _assert_no_duplicates(self, zone_fares: dict) -> None:
        """Fail fast if data contains multiple products of same type for one zone pair.
        
        Should never trigger with current data — but if it does, the
        ``dict[FareProductType, FareProduct]`` contract is broken.
        """
        for zp, products in zone_fares.items():
            types = [k for k in products if k.startswith("adult_")]
            if len(types) != len(set(types)):
                raise ValueError(f"Duplicate fare product for {zp}: {products}")
```

A caller convenience lives alongside the data — it implements the
"cheapest weekday peak return" logic without iterating lists:

```python
def cheapest_round_trip(fares: dict[FareProductType, FareProduct], national_max_single: Money | None = None) -> Money | None:
    """Cheapest way to do a weekday peak return trip.
    
    Considers: 2 × single (capped), return ticket, day ticket.
    Returns None if no fares are available.
    """
    options: list[Money] = []
    
    if FareProductType.SINGLE in fares:
        single = fares[FareProductType.SINGLE].price
        if national_max_single is not None and single > national_max_single:
            single = national_max_single
        options.append(single * 2)
    
    if FareProductType.RETURN in fares:
        options.append(fares[FareProductType.RETURN].price)
    
    if FareProductType.DAY in fares:
        options.append(fares[FareProductType.DAY].price)
    
    return min(options) if options else None
```

### 3. `CarPark` — a car park with a location and daily cost

A car park is not defined by what station it serves. The relationship is
external: the caller decides which car park is relevant for a station.

```python
# houses/car_park.py

from dataclasses import dataclass
from money import Money
from houses.geo import GeoPoint


@dataclass
class CarPark:
    """A car park with location and daily parking cost."""
    name: str
    location: GeoPoint
    daily_cost: Money | None  # None = cost unknown, Money('0', 'GBP') = free
```

The registry loads `data/parking_rates.csv` and provides lookup. Prices are
discovered from the CSV or scraped from APCOA prebook pages:

```python
class CarParkRegistry:
    """Lazy-loaded, CSV-backed car park database with APCOA fallback."""
    _by_name: dict[str, CarPark] | None = None
    _by_crs: dict[str, CarPark] | None = None  # legacy mapping from station CRS
    
    def find_near(self, location: GeoPoint, radius_km: float = 1.0) -> list[CarPark]: ...
```

## Call-site changes

The callers that currently inline-import `_shorten_station` will import
`Station` from `houses.stations` at the top level:

```python
# commute.py — top-level, no inline
from houses.stations import Station

# In summary():
#   old: _shorten_station(leg.end_station)
#   new: Station.short_name(leg.end_station)
```

`_find_bus_alternative` moves entirely into `routing.py`, where it belongs
(it's a routing function that calls Google Routes API). It uses
`BusJourneyRegistry` and `cheapest_round_trip` instead of inline-importing
from `enricher.py`:

```python
# routing.py — top-level, no inline
from houses.bus_journey import BusJourneyRegistry, cheapest_round_trip

_bus_fares = BusJourneyRegistry()

async def _find_bus_alternative(origin: str, destination: str) -> Commute | None:
    fares = _bus_fares.fares_between(origin_geo, dest_geo)
    daily_fare = cheapest_round_trip(fares, _bus_fares.national_max_single)
    ...
```

## Dependency graph after

```
stations.py        (leaf — imports only geo.py)
bus_journey.py     (leaf — imports geo.py, money)
car_park.py        (leaf — imports geo.py, money)

commute.py         ──→ stations.py (Station.short_name)
transit_route.py   ──→ stations.py (Station.short_name)

routing.py         ──→ stations.py, bus_journey.py
                   ──→ commute.py (Commute, CostGroup, etc.)

enricher.py        ──→ routing.py (top-level, no cycle!)
                   ──→ stations.py, car_park.py, commute.py

server.py          ──→ enricher.py, routing.py, etc.
```

No cycles. All imports at top level. No `global` keywords.

## Phases

### Phase A: Station 🚉

1. Install `money` dependency ✓
2. Create `houses/stations.py`:
   - `Station` dataclass (name, crs, location: GeoPoint)
   - `Station.short_name(raw)` static method
   - `Station.short` property
   - `StationRegistry` with lazy CSV loading
   - Module-level `find()`, `find_by_crs()` conveniences
3. Remove station code from `enricher.py`:
   - Delete `_shorten_station`, `_clean_station_name_for_matching`, `_lookup_station_crs`, `_lookup_station_coords`
   - Delete `_STATION_SUFFIXES`, `_STATIONS_CSV` constants
4. Update callers:
   - `commute.py` — top-level import of `Station`, use `Station.short_name()`
   - `transit_route.py` — same
   - `enricher.py` — remove old function calls, use stations module

### Phase B: BusJourney 🚌

5. Create `houses/bus_journey.py`:
   - `FareProductType` enum (SINGLE, RETURN, DAY)
   - `FareProduct` dataclass with `price: Money`, `operator`, `zone_pair`, `type`
   - `BusJourney` dataclass with `available_fares: list[FareProduct]`
   - `BusJourneyRegistry` with lazy JSON loading
   - `cheapest_round_trip(journey)` convenience function
6. Remove bus fare code from `enricher.py`:
   - Delete `_load_bus_fares`, `_compute_bus_daily_cost`, `_lookup_bus_roundtrip_cost`
   - Delete `_nearest_bus_zone`, `_nearby_bus_zones`
   - Delete `_bus_fares_data`, `_BUS_FARES_PATH` globals
7. Move `_find_bus_alternative` into `routing.py` (it's now clean — imports `BusJourneyRegistry` instead of `enricher`)

### Phase C: CarPark 🅿️

8. Create `houses/car_park.py`:
   - `CarPark` dataclass with `location: GeoPoint` and `daily_cost: Money | None`
   - `CarParkRegistry` with CSV loading and APCOA fallback
   - No station-specific fields — relationship is external
9. Remove parking code from `enricher.py`:
   - Delete `_load_parking_rates`, `_add_parking_rate_to_csv`, `_apcoa_prebook_lookup`, `_lookup_parking_cost`
   - Delete `_parking_rates_cache`, `_parking_rates_cache`, `_PARKING_RATES_PATH`

### Phase D: Break the cycle 🧹

10. Replace inline imports with top-level imports in `enricher.py`:
    - `from houses.routing import get_commute, _with_label, _drive_commute`
    - These were safe all along — the cycle is gone because routing no longer imports from enricher
11. Remove dead code: `_cached_get`, `_cached_post`
12. Verify everything works: `make format && make test`

### Phase E: Retrofit existing money fields (optional follow-up)

Existing dataclasses (`Commute.daily_cost_gbp`, `CostGroup.cost`) still use
float. Changing them to `Money` touches many callers (sheets.py, server.py,
tests). This is a separate, clearly-scoped follow-up.

## Verification

After each phase:
```bash
make format && make test
```

Final verification — no inline imports:
```bash
rg "^\s+from houses" houses/*.py  # should be zero
```

No `global` keyword (except `set_cache_dir` which is a separate fix):
```bash
rg "\bglobal\b" houses/*.py
```
