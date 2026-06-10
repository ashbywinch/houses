# PropertyLocation — Extract Geocoding into a Domain Class

## Problem

Geocoding is scattered across two modules as free-standing functions.
Every domain concept should be a class.

## Domain Concept

**`GeoPoint`** — a coordinate pair on the Earth's surface (the value).
**`PropertyLocation`** — a property's location, with what we know about it.
May be unresolved (just postcode/address) or resolved (has coordinates).

## What PropertyLocation is NOT

- It is NOT a cache — `EnrichedProperty` is the cache (stores lat/lng).
- It does NOT know about approx vs actual — that's `EnrichedProperty`'s concern.
- It does NOT have `from_enriched()` — that's the server's orchestration.

## Design

```python
@dataclass(frozen=True)
class PropertyLocation:
    """Where a property is on the map, possibly unresolved."""

    postcode: str = ""
    address: str = ""
    coordinates: Attempt[GeoPoint] = Attempt.pending()

    async def resolve(self) -> PropertyLocation:
        """Resolve address first, then postcode.

        Only makes API calls when coordinates are pending.
        Returns a new `PropertyLocation` with coordinates populated.
        """
        if not self.coordinates.is_pending:
            return self
        result = await self._geocode_address(self.address)
        if result.is_succeeded:
            return dataclasses.replace(self, coordinates=result)
        result = await self._geocode_postcode(self.postcode)
        return dataclasses.replace(self, coordinates=result)

    @classmethod
    async def from_town(cls, town: str) -> PropertyLocation:
        """Resolve a town name (walkability enrichment)."""
        ...

    # Private helpers moved from enricher.py
    @staticmethod
    async def _geocode_postcode(postcode: str) -> Attempt[GeoPoint]: ...
    @staticmethod
    async def _geocode_address(address: str) -> Attempt[GeoPoint]: ...
    @staticmethod
    async def _geocode_nominatim(query: str) -> Attempt[GeoPoint]: ...
```

## Two PropertyLocations per property

At the enrichment entry point (server.py), two are created independently:

```python
# Actual location — may already have user-provided coordinates
if payload.actual_latitude is not None:
    actual = PropertyLocation(coordinates=Attempt.succeeded(
        GeoPoint(payload.actual_latitude, payload.actual_longitude), "user",
    ))
else:
    actual = PropertyLocation(postcode=payload.postcode, address=payload.address)
    actual = await actual.resolve()

# Approx location — from Rightmove scrape, may need resolving
approx = PropertyLocation(postcode=payload.postcode, address=payload.address)
approx = await approx.resolve()

enriched = EnrichedProperty(
    approx_latitude=approx.coordinates.value_or_none().lat,
    approx_longitude=approx.coordinates.value_or_none().lon,
    actual_latitude=actual.coordinates.value_or_none().lat,
    actual_longitude=actual.coordinates.value_or_none().lon,
)
```

## What moves into `houses/location.py`

| Item | Source | Destination |
|---|---|---|
| `geocode` body | enricher.py | `PropertyLocation._geocode_postcode` |
| `geocode_address` body | enricher.py | `PropertyLocation._geocode_address` |
| `geocode_nominatim` body | enricher.py | `PropertyLocation._geocode_nominatim` |
| `_geocode_town` body + `_TOWN_SUFFIXES` | walkability.py | `PropertyLocation.from_town` |
| URL constants (`POSTCODES_IO_URL`, `OUTCODES_IO_URL`, `ORS_GEOCODE_URL`, `NOMINATIM_URL`) | enricher.py | location.py |
| `_OUTCODE_RE`, `_END_PC_RE` | enricher.py | location.py |
| `_APIState` geocoding fields | enricher.py | location.py |
| `_geo_cache` | enricher.py | location.py (internal — postcode/address → GeoPoint lookup) |

## Phases

### Phase A — Create location.py

1. Create `houses/location.py` with `PropertyLocation` class + private helpers
2. Move constants, regex patterns, API state, `_geo_cache`
3. Keep old functions in enricher.py as thin wrappers (still work)

### Phase B — Enrichment entry point creates PropertyLocation once

Currently `_run_enrichment` (server.py) calls `_geocode_address(lookup)` then
`geocode(postcode)` in two separate places (lines 665-667 for walkability,
line 703 for geo). These are the same address+postcode each time.

Replace with a single `PropertyLocation` created at the top of the function:

```python
# Create and resolve once at the entry point
location = PropertyLocation(postcode=postcode, address=lookup or address)
location = await location.resolve()
coords = location.coordinates.value_or_none()
approx_lat = coords.lat if coords else None
approx_lng = coords.lon if coords else None
```

Then replace the scattered geocoding calls:

| Line | Current code | Replacement |
|------|-------------|-------------|
| 665-667 | `_geocode_address(lookup)` then `geocode(postcode)` | Just use `coords` from above |
| 695-704 | `_geocode_address(lookup)` for approx lat/lng | Use `approx_lat`, `approx_lng` from above |

Also skip geocoding when the sheet already has coordinates (re-enrichment):

```python
location = PropertyLocation(postcode=postcode, address=lookup or address)
if approx_lat is not None and approx_lng is not None:
    location = location.resolved(GeoPoint(approx_lat, approx_lng), "sheet")
else:
    location = await location.resolve()
```

Where `resolved()` is a small helper added to `PropertyLocation`:

```python
def resolved(self, point: GeoPoint, source: str) -> PropertyLocation:
    return dataclasses.replace(self, coordinates=Attempt.succeeded(point, source))
```

### Phase C — Clean up

1. Delete old wrapper functions from enricher.py
2. `transit_route.py` geocode fallback: create `PropertyLocation` inline (genuinely one-shot)
3. Update `walkability.py` `_geocode_town` → `PropertyLocation.from_town`
