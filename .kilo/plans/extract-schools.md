# Extract School Lookup from enricher.py into schools.py

## Problem

`houses/enricher.py` is 1273 lines — more than double the 500-line limit — and
mixes four distinct domain concerns: TfL transit commute, bus fare computation,
petrol cost, and school lookup. The school section (lines 1061–1273, ~210 lines)
is completely self-contained: it depends on nothing else in enricher.py besides
shared imports (`GeoPoint`, `Commute`, `Attempt`, `geocode`,
`_geocode_address`, `settings`, and cache helpers). It is an ideal first
extraction, establishing the pattern for the remaining concerns.

## Design

Two separate concerns that were previously mixed together:

**1. `School`** — a school from the GIAS dataset. Inherent properties: name,
phase, gender, type of establishment, postcode, website, ofsted rating, coords.
Derived properties: fee-paying (from type), school type (from phase).

**2. Commute to school** — the journey from the property to the school. This
is a separate operation: `compute_school_commute(property_location, school)`
returns a `Commute`. Walking to school is a `Commute` with `daily_cost_gbp=0`.
A bus commute has a real cost. `Commute` already handles both.

`find_nearest()` returns `School | None` — just the school. The caller
computes the commute separately if they need it. No wrapper class,
no coupling between school data and journey data.

## School class

```python
class SchoolGender(StrEnum):
    """GIAS 'Gender (name)' column values — also used as query requirements.

    SchoolGender.BOYS   → "I need a school for my boy(s)"
    SchoolGender.GIRLS  → "I need a school for my girl(s)"
    SchoolGender.MIXED  → "My children are a mix — I need a coeducational school"
    """
    BOYS = "boys"
    GIRLS = "girls"
    MIXED = "mixed"


@dataclass(frozen=True)
class School:
    """A UK educational establishment from GIAS data."""

    # Column name constants (private — only matter inside from_GIAS_row)
    _COL_NAME: ClassVar[str] = "EstablishmentName"
    _COL_PHASE: ClassVar[str] = "PhaseOfEducation (name)"
    _COL_LOW_AGE: ClassVar[str] = "StatutoryLowAge"
    _COL_HIGH_AGE: ClassVar[str] = "StatutoryHighAge"
    _COL_GENDER: ClassVar[str] = "Gender (name)"
    _COL_TYPE: ClassVar[str] = "TypeOfEstablishment (name)"
    _COL_POSTCODE: ClassVar[str] = "Postcode"
    _COL_URN: ClassVar[str] = "URN"
    _COL_WEBSITE: ClassVar[str] = "SchoolWebsite"
    _COL_OFSTED: ClassVar[str] = "OfstedRating (name)"
    _COL_INSPECTION_YEAR: ClassVar[str] = "InspectionYear"
    _COL_LAT: ClassVar[str] = "Latitude"
    _COL_LNG: ClassVar[str] = "Longitude"

    _FEE_PAYING_TYPES: ClassVar[frozenset] = frozenset({
        "independent school",
        "other independent school",
        "independent special school",
        "non-maintained special school",
    })

    # ── Inherent school properties ──────────────────────────────────
    urn: str
    name: str
    phase: str  # raw PhaseOfEducation value from GIAS (e.g. "Primary", "Secondary")
    gender: SchoolGender
    type_of_establishment: str
    postcode: str
    website: str
    ofsted_rating: str
    inspection_year: str
    coords: GeoPoint | None

    # Age range this school admits (from GIAS StatutoryLowAge / HighAge — used as
    # fallback when phase-based age ranges don't apply, e.g. "Not applicable")
    statutory_low_age: int | None
    statutory_high_age: int | None

    # ── Age ranges by phase ─────────────────────────────────────────
    # PhaseOfEducation is clean (controlled vocabulary, 21k+ schools). The statutory
    # age ranges are unreliable for some schools (e.g. secondary schools reporting
    # age 0). Use well-known UK age bands per phase as the primary filter.
    _PHASE_RANGES: ClassVar[dict[str, tuple[int, int]]] = {
        "nursery": (2, 4),
        "primary": (4, 11),
        "middle deemed primary": (9, 13),
        "middle deemed secondary": (9, 14),
        "secondary": (11, 18),
        "16 plus": (16, 18),
        "all-through": (4, 18),
    }

    # ── Derived properties ──────────────────────────────────────────

    @property
    def fee_paying(self) -> bool:
        return self.type_of_establishment.lower() in self._FEE_PAYING_TYPES

    # ── Factory ─────────────────────────────────────────────────────

    @staticmethod
    def _try_int(raw: str) -> int | None:
        try:
            return int(raw)
        except (ValueError, TypeError):
            return None

    @classmethod
    def from_GIAS_row(cls, row: dict) -> School:
        lat = row.get(cls._COL_LAT)
        lng = row.get(cls._COL_LNG)
        raw_gender = (row.get(cls._COL_GENDER) or "").strip().lower()
        return cls(
            urn=(row.get(cls._COL_URN) or "").strip(),
            name=(row.get(cls._COL_NAME) or "").strip(),
            phase=(row.get(cls._COL_PHASE) or "").strip(),
            statutory_low_age=_try_int(row.get(cls._COL_LOW_AGE)),
            statutory_high_age=_try_int(row.get(cls._COL_HIGH_AGE)),
            gender=SchoolGender(raw_gender) if raw_gender else SchoolGender.MIXED,
            type_of_establishment=(row.get(cls._COL_TYPE) or "").strip(),
            postcode=(row.get(cls._COL_POSTCODE) or "").strip(),
            website=(row.get(cls._COL_WEBSITE) or "").strip(),
            ofsted_rating=(row.get(cls._COL_OFSTED) or "").strip(),
            inspection_year=(row.get(cls._COL_INSPECTION_YEAR) or "").strip(),
            coords=GeoPoint(float(lat), float(lng)) if lat and lng else None,
        )

    # ── Queries ─────────────────────────────────────────────────────

    def accepts(self, requirement: SchoolGender) -> bool:
        """Can this school satisfy the given requirement?

            SchoolGender.BOYS   → school must accept boys   (BOYS or MIXED)
            SchoolGender.GIRLS  → school must accept girls  (GIRLS or MIXED)
            SchoolGender.MIXED  → school must be coeducational (MIXED only)
        """
        return self.gender in (SchoolGender.MIXED, requirement)

    def accepts_age(self, child_age: int) -> bool:
        """Can a child of this age attend this school?

        Uses the phase-controlled vocabulary as primary filter (covers 93% of
        schools). Falls back to statutory age ranges for "Not applicable" and
        unknown phases.
        """
        phase_key = self.phase.lower()
        if phase_key in self._PHASE_RANGES:
            low, high = self._PHASE_RANGES[phase_key]
            return low <= child_age <= high
        # Fallback: "Not applicable" special schools, PRUs, etc.
        if self.statutory_low_age is not None and child_age < self.statutory_low_age:
            return False
        if self.statutory_high_age is not None and child_age > self.statutory_high_age:
            return False
        return True
```

## Shared function: `get_commute`

The caller shouldn't know Google, TfL, or ORS exist. They just want to know:
how do I get from A to B, given my traveler's circumstances? Extract a shared
function that encapsulates the routing decision tree:

```python
async def get_commute(
    origin_postcode: str,
    dest_postcode: str,
    *,
    has_car: bool,
    max_walk_minutes: int,
) -> Commute | None:
    """Route from origin to destination.

    Optimisation order (cheapest first):
    0. **Congestion zone check** — if destination outcode is a central London
       outcode (EC*, WC*, SW1, W1, SE1, N1, E1), skip driving entirely.
       Transit is always faster for central London.
    1. **Haversine pre-filter** — if straight-line distance already exceeds
       what's walkable in `max_walk_minutes` (at 5 km/h), skip the walking
       API call entirely. Free — no API cost.
    2. Walking API → if duration ≤ max_walk_minutes, return immediately.
    3. Transit (TfL if London, Google Routes otherwise). If has_car AND
       transit's first-leg walk exceeds max_walk_minutes, replace that walk
       with driving (park & ride).
    4. If has_car AND destination is NOT in congestion zone: driving (ORS).
    5. Pick the quicker among available options (transit, park & ride, drive).
    6. If no car and transit unavailable → return None.
    """

Both keyword parameters are required — every commute needs to specify them.
The caller describes the traveler; `get_commute` handles the rest.

**Bracknell uses the same max_walk_minutes as Simon (15).** A house within
15 minutes' walk of **any station on the Waterloo-Reading line** (Maidenhead,
Twyford, Reading, Wokingham, etc.) should consider transit: walk to origin
station → train to Bracknell Station → walk to office. If transit is faster
than driving (with `has_car=True`), `get_commute` returns transit.

**Skip driving API call for congestion zone destinations.** Central London
postcodes (EC, WC, SW1, W1, SE1, N1, E1) are inside or adjacent to the
congestion charge zone — driving is never quicker than transit there. Check the
destination postcode outcode against a known set of central London outcodes and
skip the ORS driving call. This saves API costs for every Simon/Lorena commute.

**Bug fix: walking uses API, not haversine.** The current code computes walk
time as `round(haversine_km / 5 * 60)`, which assumes the user can walk in a
straight line (i.e., fly). `get_commute` uses Google Routes walking mode for
actual walking duration instead. You already confirmed this bug fix is acceptable.

**What changes vs what stays the same:**
| Output | Change? |
|---|---|
| Walking duration to school/office | **Changes** — now uses real path distance from API |
| Haversine distance (Distance (km) sheet column) | **Stays same** — still `GeoPoint.distance_km_to()` |
| Transit duration & cost (TfL, train) | **Stays same** — same API calls, same parsing |
| Driving duration & cost (ORS) | **Stays same** — same API calls, same parsing |
| Bus fare lookup & route names | **Stays same** — same BODS data, same parsing |
| Ofsted rating, inspection year, school link | **Stays same** — raw GIAS data

**Age filter must produce identical results for our use case.** The current
`_phase_filter` checks `"primary" in phase.lower()`, which incidentally matches
"Middle deemed primary" schools (starting age ~9) for a 7-year-old. The new
`accepts_age(7)` correctly rejects those. But for our actual use case (Simon's
primary-age child in Maidenhead, where middle schools are absent), the results
are identical. If the refactoring reveals a discrepancy for a real property,
stop and rethink.

**Preserve existing output for everything else, including API workarounds.**
The current park & ride and bus fare logic is complex because Google Routes API
doesn't natively support "max walk time" or "park & ride as a mode." The
existing code works around these limitations:
- **Park & ride** (Simon): `_apply_park_and_ride_to_journeys` replaces
  first-leg walks over a threshold with driving (ORS Directions). This
  workaround moves into `get_commute` as-is.
- **Bus fare lookup** (Lorena, school): Google Routes transit response is
  post-processed with BODS bus fare data for cost calculation, and bus line
  names are extracted from transitDetails. This logic moves into `get_commute`
  as-is.

Every decision branch, fallback, and numeric output must match the current code.
`get_commute` is a wrapper around existing logic, not a simplification.

Usage examples:
- School: `get_commute(property_postcode, school.postcode, has_car=False, max_walk_minutes=20)` — child walks ≤20 min, then transit
- Simon: `get_commute(home, office, has_car=True, max_walk_minutes=15)` — transit with park & ride, or drive
- Lorena: `get_commute(home, office, has_car=False, max_walk_minutes=30)` — transit or walk, no car options
- Bracknell: `get_commute(home, office, has_car=True, max_walk_minutes=5)` — drive (office has parking, transit impractical)

## What moves into `schools.py`

**`SchoolGender` enum + `School` class** — as above. One file, one class, one
enum. No wrapper, no `Info` suffix, no ambiguity.

**`_load_schools()`** — returns `list[School]` (parses GIAS CSV into School instances)

**`find_nearest(postcode, child_age, address, *, requirement)`** — core search.
Returns `School | None`. Filters schools by age range and gender requirement.
Distance is computed internally (needed to find the nearest) but not returned.

**`compute_school_commute(property_postcode, school)`** — thin wrapper around
`get_commute(property_postcode, school.postcode, has_car=False, max_walk_minutes=20)`.
Delegates everything (walking check, transit fallback) to the shared function.
Returns `Commute | None`.

The caller:
```python
school = await find_nearest(postcode, child_age=7, address, requirement=SchoolGender.BOYS)
commute = compute_school_commute(postcode, school) if school else None
if commute:
    dist_km = round(property_coords.distance_km_to(school.coords), 2)
```

**Dead code to delete** (defined in enricher.py school section but never used
there — geocoding comes from `location.py`):
- `_geo_cache: dict[str, GeoPoint | None]`
- `ORS_GEOCODE_URL` constant
- `NOMINATIM_URL` constant

## What stays in `enricher.py`

Everything else: cached HTTP helpers, station utilities, parking rates, bus
fares, TfL transit, petrol cost.

## Dependencies

`schools.py` imports from:
- `houses.geo` → `GeoPoint`
- `houses.attempt` → `Attempt`
- `houses.location` → `geocode`, `_geocode_address`
- `houses.commute` → `Commute`, `CommuteMode`, `CostGroup`, `JourneyLeg`, `LegMode`
- `houses.routing` → `get_commute` (shared with Simon/Lorena/Bracknell commutes)
- `houses.config` → `settings`
- Standard lib: `csv`, `logging`, `re`, `contextlib`, `json`, `pathlib`

No dependency on any enricher internal function — clean extraction.

## Files Affected

| File | Change |
|---|---|
| `houses/routing.py` | **New** — shared `get_commute` function (consolidates Google Routes, TfL, ORS, walking logic) |
| `houses/schools.py` | **New** — `SchoolGender`, `School`, `find_nearest`, `compute_school_commute` |
| `houses/enricher.py` | Remove school section; update Simon/Lorena/Bracknell commutes to use `get_commute`; shrink from 1273→~750 |
| `houses/property.py` | Delete `SchoolInfo` Pydantic model |
| `houses/server.py` | Import from `houses.schools` instead of `houses.enricher` for school functions |
| `houses/sheets.py` | Rewrite `_fmt_*` helpers to accept `School | None` and `Commute | None` instead of `SchoolInfo` |
| `tests/unit/test_enricher.py` | Update imports; rewrite dict-based tests to use `School` |
| `tests/unit/test_sheets.py` | Update `SchoolInfo` references |
| `tests/integration/test_server.py` | Check for school-related imports |

## Steps

### 0. Create `houses/routing.py` with `get_commute()`

Single function consolidating all existing commute logic:

```python
# Congestion zone destinations (central London) — skip driving entirely
_CONGESTION_ZONE_OUTCODES = frozenset({
    "EC1", "EC2", "EC3", "EC4",
    "WC1", "WC2",
    "W1", "W2", "W8", "W11", "W14",
    "SW1", "SW3", "SW5", "SW7", "SW10",
    "SE1", "SE11",
    "N1",
    "E1", "E2", "E14",
})


async def get_commute(
    origin_postcode: str,
    dest_postcode: str,
    *,
    has_car: bool,
    max_walk_minutes: int,
) -> Commute | None:
    """Route from origin to destination.

    Optimisation order (cheapest first):
    0. Congestion zone check — skip driving for central London.
    1. Haversine pre-filter — if straight-line > walkable, skip walking API.
    2. Walking API — if ≤ max_walk_minutes, return immediately.
    3. Transit (TfL for London-area postcodes, Google Routes otherwise).
       If has_car AND transit first-leg walk > max_walk_minutes:
       replace with driving (park & ride).
    4. If has_car AND not congestion zone: driving via ORS.
    5. Pick quicker among available options.
    6. If no car + no transit → None.
    """
```

Implementation details (all existing logic, moved as-is):
- **Walking**: call Google Routes walking mode API (replaces haversine bug).
- **Transit (London)**: reuse `TransitRoute.plan()` from `transit_route.py`
  (calls TfL API). Includes park & ride logic from `_apply_park_and_ride_to_journeys`.
- **Transit (non-London)**: reuse Google Routes transit call from
  `_find_bus_alternative` (address-based, includes BODS bus fare lookup).
- **Driving**: reuse ORS Directions call from `compute_petrol_cost`.
- **Bus fare fallback**: reuse `_lookup_bus_roundtrip_cost` and
  `_nearby_bus_zones` from enricher.py (these stay in enricher.py; get_commute
  imports them).

After creating this, update `compute_simon_commute`, `compute_lorena_commute`,
and `compute_petrol_cost` in enricher.py to delegate to `get_commute` with the
appropriate parameters.

### 1. Create `houses/schools.py`

New module with `SchoolGender`, `School`, `_load_schools()`, and a single
generic `find_nearest()` function. No convenience wrappers — the caller passes
phase and requirement as parameters.

### 2. Delete `SchoolInfo` from `houses/property.py`

The Pydantic model `SchoolInfo` is no longer needed. Callers that need school
enrichment data now get `(School, Commute)` from `find_nearest` and write the
relevant fields to the sheet. Update any `import` of `SchoolInfo` across the
codebase.

### 3. Strip school code from `enricher.py`

Delete lines 1061–1273 (everything from `# --- Schools ---` to end of file).
Remove `SchoolInfo` from enricher imports (already deleted). Keep `Attempt` —
still used by petrol cost.

### 4. Update `server.py`

```python
from houses.schools import find_nearest, compute_school_commute
```

Replace the old combined lookup + commute with two separate calls:
```python
school = await find_nearest(postcode, child_age=7, address, requirement=SchoolGender.BOYS)
commute = compute_school_commute(postcode, school) if school else None
```

Write `school.name`, `school.phase`, `school.ofsted_rating`,
`commute.duration_minutes` etc to the sheet instead of flat `SchoolInfo` fields.

### 5. Update `sheets.py`

Replace `_fmt_school_link(s: SchoolInfo | None)` etc with versions that accept
`School | None` and `Commute | None`. The sheet-writing functions no longer
reference `SchoolInfo`.

### 6. Update tests

- `test_enricher.py`: Replace `_boys_eligible(school_dict)` etc with
  `School.from_GIAS_row(dict).accepts(...)` etc. Assertions stay the same.
- `test_sheets.py`: Update any `SchoolInfo` references.
- `test_server.py`: Update any school-related imports.

### 7. Verify

```bash
make format && make test && make lint
```

## Sheet Output Must Be Identical

The school columns in the sheet must produce exactly the same values as today.
The new pipeline is:

```
school = await find_nearest(postcode, child_age=7, address, requirement=...)
commute = compute_school_commute(postcode, school) if school else None
dist_km = round(property_coords.distance_km_to(school.coords), 2) if school and school.coords else None
```

| Sheet Column | Current source | New source | Format |
|---|---|---|---|
| Primary School | `SchoolInfo.name` | `school.name` | same |
| Primary Distance (km) | `SchoolInfo.distance_km` | `dist_km` (from haversine via GeoPoint) | `"{:.2f}"` |
| Primary Walk (min) | `SchoolInfo.walking_time_minutes` | `commute.duration_minutes` | `str()` |
| Primary School Link | `SchoolInfo.urn → URL` | `school.urn → URL` | same |
| Primary Ofsted | `SchoolInfo.ofsted_rating` | `school.ofsted_rating` | same |
| Primary Inspection Year | `SchoolInfo.inspection_year` | `school.inspection_year` | same |
| Secondary School | `SchoolInfo.name` | `school.name` | same |
| Secondary Distance (km) | `SchoolInfo.distance_km` | `dist_km` (from haversine via GeoPoint) | `"{:.2f}"` |
| Secondary Walk (min) | `SchoolInfo.walking_time_minutes` | `commute.duration_minutes` (walking) | `str()` |
| Secondary School Link | `SchoolInfo.urn → URL` | `school.urn → URL` | same |
| Secondary Ofsted | `SchoolInfo.ofsted_rating` | `school.ofsted_rating` | same |
| Secondary Inspection Year | `SchoolInfo.inspection_year` | `school.inspection_year` | same |
| Secondary Bus (min) | `SchoolInfo.bus_time_minutes` | `commute.duration_minutes` (bus) | `str()` |
| Secondary Bus Route | `SchoolInfo.bus_route` | `commute.summary()` or route description | same |

**Key invariants that must not change:**
- Distance is great-circle haversine (`GeoPoint.distance_km_to`), formatted to 2 decimal places — identical computation to today
- Walk time is `round(dist_km / 5 * 60)` — same formula as today, lives inside `compute_school_commute`
- Bus time is from Google Routes API (only for secondary when walk > 20 min) — same API call, same parsing
- Bus route is the transit line name + departure stop description — same logic
- Ofsted rating and inspection year are raw strings from GIAS — passed through unchanged
- School link is `https://get-information-schools.service.gov.uk/Establishments/Establishment/Details/{urn}`
- Empty/missing values are `""` (empty string), not `"None"` or `"0"`

## Expected Improvement

| Metric | Before | After |
|---|---|---|
| `enricher.py` line count | 1273 | ~1063 (–210) |
| Domain classes for school data | `SchoolInfo` (vague wrapper) | `School` (the thing itself) |
| Dict-based data clump | passed to 4 functions | replaced by `School` class |
| `SchoolInfo` model | defined in `property.py` | deleted — replaced by `(School, Commute)` |
| Dead code (`_geo_cache` etc) | 3 unused definitions | 0 |
| Duplicated URL constants | 4 files | 3 files |

## Future Work (not in scope)

- Extract petrol cost → `houses/petrol.py` (~75 lines)
- Extract transit + bus fares → `houses/transit.py` (~680 lines)
- Single‑source‑of‑truth for URL constants (`ORS_GEOCODE_URL`, `NOMINATIM_URL`)
