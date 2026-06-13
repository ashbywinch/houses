# Consolidate Rail Fare Code

## Problem

Rail fare logic is scattered across three modules with inconsistent types:

1. **`houses/rail_fares.py`** — bare functions that re-read CSVs on every call, return raw `dict` objects instead of domain types.
2. **`houses/enrichment_runner.py`** — `_enrich_rail_fares` (100-line fallback) monkeypatched in tests.
3. **`houses/services.py`** — `_DefaultRailFare` inline imports `_enrich_rail_fares`.

Also **`data/stations.csv`** is loaded twice: once by `stations.py` (creating `Station` objects) and once by `rail_fares.py` (creating raw `dict` objects).

And `Commute.daily_cost_gbp` is `float` — every monetary value in the rail fare chain should be `Money`.

## Structure

Two sequential commits, each with tests written first:

**Commit 1:** `Commute.daily_cost_gbp: float | None` → `Money | None`
**Commit 2:** Rail fare consolidation (`RailFareRegistry`, Station reuse, etc.)

The test-first order means we write ALL new tests against the CURRENT code
first (they'll fail), then implement the refactoring to make them pass, one
commit at a time.

---

## Step 0: Write all tests first (red phase)

Write these BEFORE touching any production code. They should compile but
the new tests will fail because:
- `Commute.daily_cost_gbp` is still `float` (Commit 1 tests fail)
- `RailFareRegistry` doesn't exist yet (Commit 2 tests fail)
- Old `TestEnrichRailFares` tests still use monkeypatch (will be rewritten)

### 0a. New: `tests/unit/test_commute_money.py`

Tests for the Money boundary on `Commute`:

1. `test_daily_cost_money_type` — `Commute(daily_cost_gbp=Money("15.0", "GBP")).daily_cost_gbp` is `Money`
2. `test_daily_cost_none` — `Commute().daily_cost_gbp` is `None`
3. `test_money_arithmetic` — Adding two Money values works, multiplying by int works
4. `test_money_comparison` — `Money("10.0", "GBP") == Money("10.0", "GBP")` is True
5. `test_fmt_cost_money` — `_fmt_cost(Money("10.0", "GBP"))` returns `"10.0"`
6. `test_fmt_cost_none` — `_fmt_cost(None)` returns `""`

### 0b. New: `tests/unit/test_rail_fares.py`

Tests for `RailFare` dataclass and `RailFareRegistry` lookups, all via path injection:

1. `test_rail_fare_dataclass` — stores origin Station, destination Station, Money price
2. `test_fare_between_exact_match` — temp CSV with WOK→VIC=17.00, asserts Money("17.00", "GBP")
3. `test_fare_between_reverse_match` — temp CSV with VIC→WOK=17.00, same result
4. `test_fare_between_no_match` — temp CSV with WOK→PAD, queries WOK→VIC, returns None
5. `test_nearest_station_returns_station` — StationRegistry with a single station, nearest returns it
6. `test_nearest_station_returns_closest` — two stations at different distances, closer one wins
7. `test_nearest_station_no_data` — empty registry returns None
8. `test_nearest_station_uses_geopoint` — passes GeoPoint, not lat/lng

### 0c. New: `tests/unit/test_enrich_rail_fares.py`

Tests for the enrichment outcome — verify actual Money daily cost of the commute:

9. `test_enrich_adds_rail_cost_to_simon` — simon commute with bus-only cost gets rail fare added, verify exact Money daily cost
10. `test_enrich_adds_rail_cost_to_lorena` — symmetrical
11. `test_enrich_skips_when_tfl_has_fare` — full fare already present, nothing changes
12. `test_enrich_origin_from_route_leg` — when commute has a rail leg, that leg's station is used as origin instead of nearest
13. `test_enrich_no_station_returns_unchanged` — geocode returns None, commutes unchanged

These inject `_registry` and `_geocode` kwargs (no monkeypatch):
```python
stations_csv = tmp_path / "stations.csv"
stations_csv.write_text("stationName,crsCode,lat,long\nWoking,WOK,51.317,-0.556\n")
rail_csv = tmp_path / "rail_fares.csv"
rail_csv.write_text("origin_crs,dest_crs,single_fare_gbp\nWOK,FST,17.00\n")

registry = RailFareRegistry(
    station_registry=StationRegistry(_stations_csv=stations_csv),
    _fares_csv=rail_csv,
)
async def mock_geocode(_):
    return Attempt.succeeded(GeoPoint(51.317, -0.556), "test")

result = await _enrich_rail_fares(..., _registry=registry, _geocode=mock_geocode)
assert result.daily_cost_gbp == Money("43.60", "GBP")
```

### 0d. Update `tests/unit/test_enricher.py::TestEnrichRailFares`

Rewrite the 3 existing tests to:
- Use `_registry` and `_geocode` kwargs instead of monkeypatch
- Test 1: use WOK→FST in CSV (exact pair, no London-terminal fallback)
- Test 2: use BKO→VIC in CSV (already exact)
- Test 3: no CSV data needed (skips rail entirely)
- Use `== Money(...)` not `pytest.approx`

### 0e. Update `tests/unit/test_stations.py`

Add tests for `StationRegistry.nearest()`:
- `test_nearest_returns_closest_station`
- `test_nearest_returns_none_when_empty`

### 0f. Update existing tests for Money field

Every file that constructs `Commute(daily_cost_gbp=float)` must change to `Commute(daily_cost_gbp=Money(str(X), "GBP"))`.
Every assertion `c.daily_cost_gbp == float` must change to `c.daily_cost_gbp == Money(str(X), "GBP")`.
Every `pytest.approx` on daily_cost_gbp becomes exact `== Money(...)`.

Files to update (~20 files):
- `tests/unit/test_enricher.py` — all `daily_cost_gbp=` constructors
- `tests/unit/test_routing.py` — `_WALK_60`, `_WALK_20`, `_TRANSIT_30`, `_DRIVE_25` etc.
- `tests/unit/test_sheets.py` — `daily_cost_gbp=8.50` etc.
- `tests/unit/test_sheet_update.py`
- `tests/integration/test_server.py`
- `tests/helpers.py` — `FakeCommuteRouter` defaults
- `houses/enricher.py` — reads daily_cost_gbp (accessor stays same, just the type changes)

### 0g. Run tests — expect failures (red)

Run `make test`. The new tests will fail because:
- `Commute.daily_cost_gbp` is still `float` — Money constructors/comparisons will error
- `RailFareRegistry` doesn't exist
- `_enrich_rail_fares` doesn't have `_registry`/`_geocode` kwargs
- `StationRegistry` doesn't have `nearest()` or path injection
- `_fmt_cost` receives Money but expects float

Count the failures and verify they match what we expect to fix.

---

## Step 1: Commit 1 — Commute.daily_cost_gbp → Money

### What changes

The `Commute` dataclass field:
```python
# Before
daily_cost_gbp: float | None = None

# After
daily_cost_gbp: Money | None = None
```

### Changes per caller type

| Pattern | Before | After |
|---------|--------|-------|
| Construction | `daily_cost_gbp=15.0` | `daily_cost_gbp=Money("15.0", "GBP")` |
| Construction (None) | `daily_cost_gbp=None` | No change |
| Null check | `c.daily_cost_gbp is None` | No change (Money \| None) |
| Zero check | `c.daily_cost_gbp == 0.0` | `c.daily_cost_gbp == Money("0", "GBP")` |
| Truthiness | `c.daily_cost_gbp or ...` | Replace with `c.daily_cost_gbp is not None and ...` |
| Sheet format | `_fmt_cost(val: float \| None)` | `_fmt_cost(val: Money \| None)` |

### Files to modify

- `houses/commute.py` — field type
- `houses/enrichment_runner.py` — `_enrich_rail_fares` Commute constructors, `run_enrichment` Commute constructor
- `houses/enricher.py` — reads `daily_cost_gbp` for breakdown (type only, logic same)
- `houses/routing.py` — Commute constructors, `_tiebreak` comparison
- `houses/transit_route.py` — Commute constructor, local variable
- `houses/sheets/row.py` — `_fmt_cost` signature
- `tests/helpers.py` — `FakeCommuteRouter` defaults
- ~15 test files — `daily_cost_gbp=` constructor args and assertions
- `scripts/sync_parking_rates.py` — incidental, just the column header `daily_cost_gbp` in CSV writing (not actual Money usage)

### Guard

Run `make test` — all tests pass (including the new ones from Step 0).

---

## Step 2: Commit 2 — Rail fare consolidation

### 2a. StationRegistry: nearest() + path injection

Add to `houses/stations.py`:

```python
def __init__(self, _stations_csv: Path | None = None) -> None:
    self._stations = None
    self._by_crs = None
    self._csv_path = _stations_csv or _STATIONS_CSV

def nearest(self, point: GeoPoint) -> Station | None:
    """Return the station closest to point."""
    self._load()
    if not self._stations:
        return None
    best = None
    best_dist = float("inf")
    for station in self._stations.values():
        d = point.distance_km_to(station.location)
        if d < best_dist:
            best_dist = d
            best = station
    return best
```

Also update `_load()` to use `self._csv_path` instead of the module-level `_STATIONS_CSV`.

### 2b. RailFareRegistry (houses/rail_fares.py)

Replace module-level functions with a pure data registry. No enrichment logic, no Commute references, no `LONDON_CRS`.

```python
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

from money import Money

from houses.geo import GeoPoint
from houses.stations import Station, StationRegistry


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RailFare:
    """A single fare between two stations."""
    origin_crs: str
    dest_crs: str
    single_fare_gbp: Money


class RailFareRegistry:
    """Lazy-loaded registry of rail fare data.

    Loads data/rail_fares.csv on first query and caches the result.
    Uses StationRegistry for station lookups (no duplicate CSV loading).
    No enrichment logic — pure data lookup.
    """

    def __init__(
        self,
        station_registry: StationRegistry | None = None,
        _fares_csv: Path | None = None,
    ):
        self._station_registry = station_registry or StationRegistry()
        self._fares_csv = _fares_csv or Path("data/rail_fares.csv")
        self._fares_by_pair: dict[frozenset[str], Money] | None = None

    def _load(self) -> None:
        if self._fares_by_pair is not None:
            return
        fares: dict[frozenset[str], Money] = {}
        if not self._fares_csv.is_file():
            logger.warning("Rail fares CSV not found at %s", self._fares_csv)
            self._fares_by_pair = fares
            return
        with self._fares_csv.open(newline="") as f:
            for row in csv.DictReader(f):
                origin = (row.get("origin_crs") or "").strip().upper()
                dest = (row.get("dest_crs") or "").strip().upper()
                cost_str = (row.get("single_fare_gbp") or "").strip()
                if origin and dest and cost_str:
                    try:
                        fares[frozenset({origin, dest})] = Money(cost_str, "GBP")
                    except Exception:
                        continue
        self._fares_by_pair = fares

    def nearest_station(self, point: GeoPoint) -> Station | None:
        return self._station_registry.nearest(point)

    def find_station_by_crs(self, crs: str) -> Station | None:
        return self._station_registry.find_by_crs(crs)

    def fare_between(self, origin: Station, destination: Station) -> Money | None:
        """Return the single fare between two stations.

        Tries exact origin→destination, then reverse (fares are symmetric
        for singles). Returns None if no fare exists for this pair.
        No London-terminal fallback — different terminals have different fares.
        """
        self._load()
        if not self._fares_by_pair:
            return None
        return self._fares_by_pair.get(frozenset({origin.crs, destination.crs}))
```

Remove from the file:
- `_STATIONS_CSV` (no longer needed)
- `_load_stations()` (duplicate of StationRegistry)
- `_load_fares()` (replaced by `RailFareRegistry._load()`)
- `nearest_station(lat, lng)` (replaced by `RailFareRegistry.nearest_station(GeoPoint)`)
- `fare_between(origin_crs, dest_crs)` (replaced by `RailFareRegistry.fare_between(Station, Station)`)
- `LONDON_CRS` (removed — the London-terminal fallback was a bodge)

### 2c. Context var (houses/context.py)

```python
from houses.rail_fares import RailFareRegistry

_request_rail_fares: contextvars.ContextVar[RailFareRegistry | None] = contextvars.ContextVar(
    "_request_rail_fares", default=None
)

def get_rail_fare_registry() -> RailFareRegistry:
    reg = _request_rail_fares.get()
    if reg is None:
        reg = RailFareRegistry()
        _request_rail_fares.set(reg)
    return reg
```

### 2d. Update enrichment_runner.py

`_enrich_rail_fares` gets `_registry` and `_geocode` kwargs. Uses `RailFareRegistry`
for all lookups, `Station` instead of dicts, `GeoPoint` instead of `(lat, lng)`.

The `_origin_station` helper returns `Station | None` instead of `dict | None`.

Import changes:
- Remove `from houses.rail_fares import fare_between, nearest_station`
- Add `from houses.rail_fares import RailFareRegistry`
- Add `from houses.context import get_rail_fare_registry`

### 2e. Wire middleware (houses/server.py)

No token needed — `get_rail_fare_registry()` auto-creates on first use.

### 2f. Update services.py

No change needed — `_DefaultRailFare.enrich()` already imports `_enrich_rail_fares`
from `enrichment_runner.py` with the correct path.

### Guard

Run `make test` — all tests pass.

---

## Execution Order (Summary)

```
Step 0:  Write ALL tests (new + updated). Run → RED.
Step 1:  Commit 1 — Commute Money change. Run → GREEN.
Step 2:  Commit 2 — Rail fare consolidation. Run → GREEN.
Step 3:  Delete plan file.
```

## Files Affected

| File | Step 0 (tests) | Step 1 (Money) | Step 2 (registry) |
|------|---------------|----------------|-------------------|
| `houses/commute.py` | — | `daily_cost_gbp: Money` | — |
| `houses/transit_route.py` | — | Money construction | — |
| `houses/routing.py` | — | Money construction | — |
| `houses/enricher.py` | — | Money reads | — |
| `houses/sheets/row.py` | — | `_fmt_cost` signature | — |
| `houses/enrichment_runner.py` | — | Money construction | Use RailFareRegistry |
| `houses/stations.py` | — | — | +`nearest()`, +path injection |
| `houses/rail_fares.py` | — | — | RailFareRegistry, remove old |
| `houses/context.py` | — | — | +`_request_rail_fares` |
| `tests/unit/test_commute_money.py` | **New** | — | — |
| `tests/unit/test_rail_fares.py` | **New** | — | — |
| `tests/unit/test_enrich_rail_fares.py` | **New** | — | — |
| `tests/unit/test_enricher.py` | Rewrite 3 tests | Money assertions | Update injection |
| `tests/unit/test_stations.py` | Add nearest() tests | — | — |
| ~15 other test/script files | Money updates | Money updates | — |
