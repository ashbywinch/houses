# Next Phase: ContextVar + Middleware + Local DI

## Principle

**ContextVar is for truly global per-request state** (bus fare registry shared across modules, which services are active). **Local DI (constructor/function parameters) is for implementation details** (car park CSV data, mock pages, sheet IDs). Don't put everything in context.

## What Should Go in Context

Only things that are:
- Shared across multiple modules at runtime
- Stateful (loaded from CSV/API, cached, mutated during a request)
- Painful to pass through function signatures

### BusJourneyRegistry — YES, ContextVar

Currently duplicated as module-level singletons in `routing.py` and `transit_route.py`:

```python
# routing.py
_bus_fares = BusJourneyRegistry()

# transit_route.py
_bus_fares = BusJourneyRegistry()
```

Both load from `data/bus_fares.csv` at import time. Tests monkeypatch CSV paths or the whole function. Moving to per-request context means:
- Each request gets a fresh, correctly-scoped registry
- Tests set up a pre-populated registry without monkeypatching

### Geo rate-limit state — YES, ContextVar

```python
# location.py
class _GeoState:
    ors_geo_exhausted: bool = False
    nominatim_exhausted: bool = False
    nominatim_last_call: float = 0.0

_geo_state = _GeoState()
```

Module-level mutable state that persists across requests. This should be per-request.

### Sheets client — YES, ContextVar (replaces 14 patches)

Tests currently do `patch("houses.server.get_client", return_value=mock_client)`. A `_request_sheets_client` context var means:
- Production: middleware sets the real client
- Tests: fixture sets a mock client
- No `try/finally`, no `with patch`

### Services dataclass — Already done (Stage 7)

Just needs to be wired to context instead of the `services` parameter on `_run_enrichment`.

## What Should NOT Go in Context

### CarParkRegistry — Local DI

Currently instantiated inside a method body: `parking = CarParkRegistry()`. Instead, make it accept data directly:

```python
@dataclass
class CarParkRegistry:
    _car_parks: list[CarPark] = field(default_factory=_load_car_parks)

    @staticmethod
    def _load_car_parks() -> list[CarPark]:
        # reads from _PARKING_RATES_PATH
        ...

    @classmethod
    def from_csv(cls, path: Path) -> CarParkRegistry: ...
    @classmethod
    def from_car_parks(cls, car_parks: list[CarPark]) -> CarParkRegistry: ...
```

Then tests that need a parking cost do:
```python
registry = CarParkRegistry.from_car_parks([
    CarPark(station_name="Fleet", crs="FLE", daily_cost=Money("10.90", "GBP")),
])
```

And `TransitRoute._add_parking_cost` accepts an optional registry:
```python
async def _add_parking_cost(self, data, current_cost, _registry=None):
    registry = _registry or get_car_park_registry()  # or CarParkRegistry()
    car_park = registry.find_car_park(station)
```

No ContextVar needed. The one existing test (`TestParkAndRideCostGroup`) that monkeypatches `_PARKING_RATES_PATH` to a temp CSV instead just passes a `CarParkRegistry.from_car_parks(...)`.

### Council tax rates CSV — Local DI

`_lookup_yearly_cost` reads from `data/council_tax_rates.csv`. Tests that need a specific rate can pre-populate the CSV fixture or inject a rate directly via local DI. Not worth a context var.

### Town description cache — Local DI or leave as module-level

`_town_cache` in `town_desc.py` is an in-memory cache. It's harmless across requests (just avoids duplicate LLM calls). Not worth refactoring unless it causes test isolation issues.

### API cache directory — Already handled

`set_cache_dir()` in `api_cache.py` is already handled by the integration conftest's `_isolate_api_cache` fixture. Works fine.

### Parking CSV path — Local DI (as discussed)

`_PARKING_RATES_PATH` in `car_park.py` is used by `CarParkRegistry._load_from_csv()`. If we add `from_car_parks()` classmethod, tests never need to touch the CSV path at all.

### Rightmove sample page — Local DI

Currently `settings.rightmove_sample_page` is mutated for a single test that needs a fixture HTML page. The function `scrape_rightmove` should accept an optional source parameter:

```python
async def scrape_rightmove(url: str, _sample_page: str | None = None) -> dict:
    page = _sample_page or settings.rightmove_sample_page
    ...
```

Then the test passes the fixture path directly instead of mutating settings.

### Sheet ID — Not needed

If sheets client is in context, `settings.sheet_id` is never read by any code path that tests exercise. The existing `_no_sheet_writes` conftest fixture empties it as a safety net — fine to keep.

## Summary: Three Categories

| Category | Examples | Mechanism |
|----------|----------|-----------|
| **Global per-request state** | `BusJourneyRegistry`, geo state, sheets client, Services | ContextVar + middleware |
| **Local data objects** | CarParkRegistry data, CSV contents, pages, rates | Constructor/parameter injection |
| **Test infrastructure** | API cache dir, sheet ID safety net | Keep existing fixtures |

## Migration Plan

### Phase E: Local DI for CarParkRegistry

- Add `CarParkRegistry.from_car_parks(car_parks)` classmethod
- Have `_add_parking_cost` accept optional `_registry` parameter with default `None` → creates default registry
- Convert `TestParkAndRideCostGroup` to pass registry directly instead of monkeypatching CSV path
- Remove `_PARKING_RATES_PATH` monkeypatch from existing tests

### Phase F: ContextVar infrastructure

- Create `houses/context.py` with `_request_services`, `_request_bus_fares`, `_request_geo_state`, `_request_sheets_client`
- Each getter auto-creates defaults when context is empty (production) but allows test fixtures to override
- Wire into `server.py` middleware
- Add `get_services()` — deprecate the `services` parameter on `_run_enrichment` by reading from context instead

### Phase G: Convert BusJourneyRegistry to context

- `routing.py`: replace `_bus_fares = BusJourneyRegistry()` with `get_bus_fare_reader()`
- `transit_route.py`: same
- Tests create a `BusJourneyRegistry` with test data and set it on context
- This eliminates ~15 monkeypatch calls (the CSV path hacks in routing and transit_route tests)

### Phase H: Convert sheets client to context

- `houses/sheets.py`: `get_client()` checks context first, falls back to real credentials
- `houses/context.py`: `_request_sheets_client` var
- `test_server.py`: `_mock_sheet` fixture sets context instead of `patch("houses.server.get_client")`
- Removes 14 `patch` calls

### Phase I: Convert geo state to context

- `location.py`: `_geo_state` reads from context, middleware sets fresh `_GeoState()` per request
- `_geo_cache_var` already works — just keep it
- Eliminates test isolation issues with rate-limit state

### Phase J: Rightmove sample page local DI

- `scrape_rightmove` accepts optional `_page_path` parameter
- The one test that uses `rightmove_sample_page` passes it directly
- No more `settings` mutation in tests

## Scripts Compatibility

Of the 12 scripts in `scripts/`:

| Script | Dependency on Houses | Needs ContextVar Setup? |
|--------|---------------------|------------------------|
| `refresh_columns.py` | Calls `_run_backfill_enrichment` + `get_client()` | No — lazy getters auto-create defaults |
| `update_sheet.py` | Calls enrichment via `TestClient` | No — TestClient provides request scope |
| `sheet_tool.py` | Pure `houses.sheets` utilities | No |
| `deploy_script.py` | Reads `settings` | No |
| 8 others | Completely standalone | No |

`refresh_columns.py` calls `_run_backfill_enrichment` and `get_client()` directly. Since all lazy getters auto-create production defaults when context is empty, this script works unchanged. The only requirement: getters must not panic when ContextVars are unset.

## Open Questions

1. **Accidental capture in ContextVars?** If a function starts an async task that outlives the request, the context is still attached. Not a concern for this codebase — no background tasks.

2. **Parallel migration?** All phases in one PR. Phase E (CarPark local DI) touches `car_park.py` and `transit_route.py`. Phase F-J (ContextVar infrastructure) touches `context.py`, `server.py`, `routing.py`, `location.py`, `sheets.py`, and test files. No file overlap — can be done with subagents in parallel.
