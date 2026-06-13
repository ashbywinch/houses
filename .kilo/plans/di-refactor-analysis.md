# DI Refactoring Plan

## Context

You're refactoring the enrichment monolith into dedicated modules. Recent commits (Stages 1-5) removed `_google_transit_commute`, simplified `get_commute` to TfL-only, and extracted pure parsing functions (`_parse_google_steps`, `_parse_tfl_legs`). This was the right direction.

The remaining architectural issue: **agents can't find the seams** when writing tests. Every function hard-wires its dependencies via global imports. The circular `enricher.py` ↔ `transit_route.py` dependency (hidden by lazy imports) makes the dependency graph non-obvious.

The "2m" test (`_apply_park_and_ride_to_journeys` in `test_enricher.py`) illustrates this: to test "does a 35-min walk get replaced with driving?", you need to discover that `_get_drive_minutes` is the right seam — which itself depends on `geocode` (3 backends) → `find_station` (CSV) → ORS API (httpx). An agent can't find this without reading the full dependency chain.

## Current State

### Dependency Graph (still has cycle)

```
server.py → calls 10+ services directly

enricher.py (has TfL helpers + park-and-ride + petrol + commute orchestration)
  ├─ → location.py (DIRECT: geocode, _geocode_address)
  └─ → routing.py (LAZY: get_commute, _drive_commute)

routing.py (commute decision logic)
  ├─ → location.py (LAZY: geocode, _geocode_address) [inside _drive_commute]
  └─ → transit_route.py (LAZY: TransitRoute) [inside _tfl_transit_commute]

transit_route.py (TfL routing, parking, bus fares)
  ├─ → enricher.py (DIRECT: _apply_park_and_ride, _next_weekday_date_params,
  │                       _pick_best_journey, _tfl_auth_params) ← CYCLIC!
  ├─ → routing.py (DIRECT: _bus_fare_for)
  └─ → location.py (DIRECT: _geocode_address, geocode)

schools.py → location.py (DIRECT) + routing.py (LAZY)
walkability.py → location.py (LAZY)
location.py → api_cache.py, retry.py (infrastructure only)
```

### What Tests Look Like Today

| File | Approach |
|------|----------|
| `test_routing.py` (unit) | **6 `monkeypatch` per test** — patches `_walk_commute`, `_tfl_transit_commute`, `_drive_commute`, `_in_congestion_zone` |
| `test_enricher.py` (integration) | **`patch("houses.enricher._get_drive_minutes")`** — knows the right seam |
| `test_transit_route.py` (unit) | **`monkeypatch` + `tmp_path`** for CSV paths, patches `TransitRoute.plan` |
| `test_server.py` (integration) | **25 `patch` calls** — mocks enrichment functions + sheet client |
| Integration conftest | **157 lines** MockTransport infrastructure for all 9 integration files |

## The Plan (4 phases, no `_kwarg` on internal functions)

### Phase A: Break the Circular Dependency

Move all TfL-related helpers from `enricher.py` into `transit_route.py`:

| Function | Currently in | Move to |
|----------|-------------|---------|
| `_next_weekday_date_params` | `enricher.py` | `transit_route.py` |
| `_format_route_summary` | `enricher.py` | `transit_route.py` |
| `_pick_best_journey` | `enricher.py` | `transit_route.py` |
| `_tfl_auth_params` | `enricher.py` | `transit_route.py` |
| `_apply_park_and_ride_to_journeys` | `enricher.py` | `transit_route.py` |
| `_get_drive_minutes` | `enricher.py` | `transit_route.py` |
| `_cached_get` / `_cached_post` | `enricher.py` | Keep (general-purpose) or move to `api_cache.py` |

Remove `enricher.py` from `transit_route.py` imports. Remove lazy imports from `enricher.py` (they import `routing.py`, which is fine).

**This is pure code motion — no behavior change, no new tests needed.**

After Phase A, the dependency graph becomes a DAG:

```
transit_route.py → (no enricher import) → still imports routing._bus_fare_for, location
enricher.py → routing.py (direct, no lazy) → transit_route.py (direct, no lazy)
```

### Phase B: Define Service Protocols

Create `houses/services.py` with `typing.Protocol` classes:

```python
class GeocodingService(Protocol):
    async def geocode_postcode(self, postcode: str) -> Attempt[GeoPoint]: ...
    async def geocode_address(self, address: str) -> Attempt[GeoPoint]: ...

class CommuteRoutingService(Protocol):
    async def get_commute(self, origin: str, dest: str, *, has_car: bool, max_walk_minutes: int) -> Attempt[Commute]: ...

class EPCLookupService(Protocol):
    async def lookup(self, postcode: str, address: str = "") -> str: ...

class CouncilTaxService(Protocol):
    async def lookup(self, postcode: str, address: str = "") -> Attempt[CouncilTaxInfo]: ...

class WalkabilityService(Protocol):
    async def enrich(self, lat: float, lng: float, address: str) -> dict[str, Any]: ...

class TownDescService(Protocol):
    async def describe(self, town: str, postcode: str) -> str: ...

class SchoolLookupService(Protocol):
    async def find_nearest(self, postcode: str, child_age: int, address: str = "", requirement: SchoolGender = ...) -> School | None: ...
    async def school_commute(self, postcode: str, school: School) -> Commute | None: ...
```

These are **documentation-first** — agents read `services.py` to understand module boundaries. No production code changes yet.

### Phase C: Add `Services` Dataclass to `_run_enrichment`

```python
# houses/services.py
@dataclass
class Services:
    geocoder: GeocodingService = field(default_factory=_RealGeocoder)
    commute_router: CommuteRoutingService = field(default_factory=_RealCommuteRouter)
    epc_service: EPCLookupService = field(default_factory=_RealEPCLookup)
    council_tax_service: CouncilTaxService = field(default_factory=_RealCouncilTax)
    walkability_service: WalkabilityService = field(default_factory=_RealWalkability)
    town_desc_service: TownDescService = field(default_factory=_RealTownDesc)
    school_lookup_service: SchoolLookupService = field(default_factory=_RealSchoolLookup)
```

```python
# server.py
async def _run_enrichment(
    url: str, address: str, postcode: str, lookup: str,
    bedrooms: int | None = None, price: float | None = None,
    enabled: set[str] | None = None,
    actual_latitude: float | None = None, actual_longitude: float | None = None,
    services: Services | None = None,  # ← the only DI change
) -> EnrichedProperty:
    svc = services or Services()  # uses real defaults
    # ...
    simon = (await svc.commute_router.get_commute(lookup, settings.simon_postcode, has_car=True, max_walk_minutes=15)).value_or_none()
```

The `Services()` defaults construct real objects that call the existing global functions. No change to production behavior when `services` is None.

**Only `_run_enrichment` gets this parameter.** Below that layer (`compute_simon_commute`, `get_commute`, etc.), code keeps global imports and monkeypatch — which is fine, because agents can now see the boundaries documented in `services.py`.

### Phase D: Server Tests Use Fakes

```python
# tests/helpers.py
class FakeServices(Services):
    def __init__(self, **overrides):
        # Set all services to fake defaults
        self.geocoder = FakeGeocoder()
        self.commute_router = FakeCommuteRouter()
        # etc.
        for k, v in overrides.items():
            setattr(self, k, v)

class FakeGeocoder:
    def __init__(self, result: GeoPoint | None = GeoPoint(51.5, -0.1)):
        self.result = result
        self.calls: list[str] = []

    async def geocode_postcode(self, postcode: str) -> Attempt[GeoPoint]:
        self.calls.append(postcode)
        return Attempt.succeeded(self.result, "fake") if self.result else Attempt.impossible("fake", "no result")

    async def geocode_address(self, address: str) -> Attempt[GeoPoint]:
        self.calls.append(address)
        return Attempt.succeeded(self.result, "fake") if self.result else Attempt.impossible("fake", "no result")
```

Server tests become:

```python
# test_server.py — no more 25 patches
def test_backfill_creates_row(self):
    services = FakeServices(
        commute_router=FakeCommuteRouter(result=some_commute),
        epc_service=FakeEPC(band="C"),
    )
    mock_client = self._mock_sheet(view_rows=[...])
    with patch("houses.server.get_client", return_value=mock_client):
        resp = client.post("/properties")
    assert resp.status_code == 200
```

## What This Enables

1. **Agents can find seams** — `services.py` documents every module boundary. "Need to test EPC without the API? Use `FakeEPC`."

2. **Server tests drop 25 patches** — one `FakeServices()` object replaces all of them.

3. **Existing MockTransport infrastructure still works** — no need to rewrite integration tests.

4. **Gradual adoption** — new tests use fakes; old tests keep monkeypatch/MockTransport until someone migrates them.

5. **No `_kwarg` on internal functions** — `compute_simon_commute`, `get_commute`, `_walk_commute` etc. keep their current signatures. The DI boundary is only at the `_run_enrichment` level.

## What It Does NOT Change

- Internal module tests (`test_routing.py`, `test_transit_route.py`) keep using monkeypatch — that's fine, they mock at the right seam for their level.
- `enricher.py`, `routing.py`, `transit_route.py` keep using global imports internally — no change.
- The MockTransport conftest remains for any test that prefers it.

## Risks

1. **`Services()` defaults create real objects** — if those constructors do I/O at import time, there could be issues. Mitigation: factory functions should be lazy (don't create httpx clients until called).

2. **Protocol definitions drift** — if a protocol signature changes but the real implementation doesn't match, type checkers catch it. Mitigation: run `mypy` after changes.

3. **Merge conflicts** — code motion in Phase A touches `enricher.py` and `transit_route.py` heavily. Do this in its own focused PR.
