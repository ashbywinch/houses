# Plan: Generic Attempt[T] Type

## Goal

Create a reusable generic type `Attempt[T]` (naming TBD) that distinguishes three states:

| State | Meaning | Example (geocoding) |
|---|---|---|
| `Succeeded(value)` | We have the value | `(51.5, -0.13)` |
| `Pending()` | Haven't tried yet | not yet geocoded |
| `Impossible(reason, exc?)` | Tried, can't be found | address doesn't exist, ambiguous, API 429 |

## 1. New File: `houses/attempt.py`

A module with zero dependencies on other housing modules. Depends only on stdlib.

```python
from __future__ import annotations
import typing
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

T = TypeVar("T")
U = TypeVar("U")
R = TypeVar("R")


@dataclass
class Attempt(Generic[T]):
    _status: AttemptStatus  # internal enum
    _value: T | None = None
    _reason: str = ""
    _exception: BaseException | None = None

    # -- Constructors --
    @staticmethod
    def succeeded(value: T) -> Attempt[T]: ...
    @staticmethod
    def pending() -> Attempt[T]: ...
    @staticmethod
    def impossible(reason: str = "", exception: BaseException | None = None) -> Attempt[T]: ...

    # -- Predicates --
    @property
    def is_succeeded(self) -> bool: ...
    @property
    def is_pending(self) -> bool: ...
    @property
    def is_impossible(self) -> bool: ...

    # -- Extraction --
    def get(self) -> T: ...  # raises if not succeeded
    def value_or(self, default: T) -> T: ...
    def value_or_none(self) -> T | None: ...  # bridge to Optional

    # -- Functor --
    def map(self, fn: Callable[[T], U]) -> Attempt[U]: ...

    # -- Monad --
    def bind(self, fn: Callable[[T], Attempt[U]]) -> Attempt[U]: ...

    # -- Exhaustive match --
    def match(
        self,
        succeeded: Callable[[T], R],
        pending: Callable[[], R],
        impossible: Callable[[str, BaseException | None], R],
    ) -> R: ...
```

### Semantics

| Operation | `Succeeded(42)` | `Pending()` | `Impossible("404")` |
|---|---|---|---|
| `.get()` | `42` | raises `ValueError` | raises `ValueError` |
| `.value_or(0)` | `42` | `0` | `0` |
| `.map(x2)` | `Succeeded(84)` | `Pending()` | `Impossible("404")` |
| `.bind(fn)` | `fn(42)` | `Pending()` | `Impossible("404")` |
| `.is_succeeded` | `True` | `False` | `False` |

### Serialization helper

For Pydantic models, add a `to_optional()` → `T | None` that collapses both `Pending` and `Impossible` to `None` (the boundary pattern — `Attempt` lives in the enrichment pipeline, `Optional` lives at the sheet boundary).

## 2. Pydantic Integration

`Attempt[T]` is a dataclass, not a Pydantic model. For fields in `EnrichedProperty` that are currently `float | None`, add a `@field_validator` that accepts both `Attempt[T]` and `T | None`:

```python
# Or simpler: keep sheet model fields as Optional, convert at the boundary
```

**Recommended approach**: Keep `EnrichedProperty` fields as-is (`float | None`). Use `Attempt[T]` only in the enrichment pipeline (`enricher.py`, `geo.py`, etc.) and convert to `Optional` at the server/sheet boundary via `.value_or_none()`. This minimizes model churn.

Later, if the sheet model wants to store the distinction (e.g., an "Impossible" column), add a dedicated field and a custom Pydantic type.

## 3. Integration into Existing Code (Phased)

### Phase A: Geocoding functions (pilot)

**File: `houses/attempt.py`** — create the type + tests.

**File: `houses/enricher.py`** — change return types of:
- `_geocode(postcode) → Attempt[tuple[float, float]]` (postcodes.io)
- `_geocode_address(address) → Attempt[tuple[float, float]]` (Google → ORS → Nominatim)
- `_geocode_nominatim(query) → Attempt[tuple[float, float]]`

Currently these return `None` for both "not cached / haven't called API yet" AND "API returned no results". With `Attempt`, the caller can distinguish:
- `Succeeded(lat, lng)` → got coordinates, use them
- `Pending()` → shouldn't happen from the cache; the function always tries
- `Impossible("404 from postcodes.io")` → address doesn't exist, don't retry

**File: `houses/enricher.py`** — remove `_APIState` exhaustion flags. `Impossible` with the exception carries the same info (429 → exhausted).

Update callers in `_find_nearest_boys`, `compute_petrol_cost`, `_get_drive_minutes`, etc.

### Phase B: Transit enrichment

Currently `compute_transit` returns `TransitInfo` with `duration_minutes: int | None`. Change to:
- `compute_transit(...) → Attempt[TransitInfo]` where `Impossible` means TfL couldn't route
- Internally, `Attempt.pending()` maps to "cache miss + no API key configured"
- `Attempt.impossible()` means "TfL returned 404 / no route found"

### Phase C: All enrichment functions

- `compute_petrol_cost(...) → Attempt[PetrolCost]`
- `find_nearest_boys_primary(...) → Attempt[SchoolInfo]`
- `lookup_council_tax(...) → Attempt[CouncilTaxInfo]`
- `enrich_walkability(...) → Attempt[...]`

### Phase D: Server layer

`server.py` currently does:
```python
coords = await _geocode_address(lookup)
if coords is None:
    coords = await _geocode(postcode)
if coords is None:
    # can't geocode, skip walkability
```

With `Attempt`, the fallback chain becomes pattern-matched:
```python
coords = await _geocode_address(lookup)
if coords.is_pending or coords.is_impossible:
    coords = await _geocode(postcode)
# coords.match(...) for exhaustive handling
```

## 4. Tests

### New file: `tests/unit/test_attempt.py`

- Construction: `succeeded`, `pending`, `impossible`
- `get()` raises on pending/impossible
- `value_or()` returns default for non-succeeded
- `map()` propagates through succeeded, short-circuits others
- `bind()` chains attempts
- `match()` exhaustiveness
- `value_or_none()` bridge to `Optional`
- Serialization: `Attempt` type is immutable, frozen dataclass

### Integration tests (Phase A-C)

Existing tests for geocoding, transit, etc. currently assert `is None` for failure cases. Update to assert `is_impossible` and inspect the reason/exception. Use the existing `mock_httpx` fixture pattern.

## 5. Migration Strategy

| Step | Files | Effort |
|---|---|---|
| 1. Create `attempt.py` + unit tests | `houses/attempt.py`, `tests/unit/test_attempt.py` | Small |
| 2. Geocoding return types only | `houses/enricher.py` (3 functions), update callers in same file | Medium |
| 3. Server.py fallback chain | `houses/server.py` (~5 call sites) | Small |
| 4. Transit return type | `houses/enricher.py` (`compute_transit`, helpers), `houses/server.py` | Medium |
| 5. All other enrichment | `houses/enricher.py`, `houses/walkability.py`, `houses/council_tax.py`, etc. | Large |
| 6. Remove `_APIState` | `houses/enricher.py` | Small cleanup |

Steps 1-3 can be done in a single PR. Steps 4-5 are follow-ups.

## 6. Unknowns / Decisions Needed

- **Name**: `Attempt` or another? The user needs to decide.
- **Exception exposure**: Should `Impossible` always store the exception, or is `reason: str` sufficient? The user asked for optional exception.
- **Pydantic integration**: Should `Attempt[T]` be a Pydantic-compatible type (custom `__get_pydantic_core_schema__`), or keep `EnrichedProperty` as `Optional` and convert at the boundary? Recommending the latter for now.
- **Pending in enrichment**: Currently, enrichment functions always attempt when called. `Pending()` represents "not yet called" — it's the default state before a function runs. Should `Attempt.pending()` only appear as constructor default, or should cached-but-not-tried states exist in functions? I'd say constructor-only.
- **Status enum**: Keep the status enum separate (for matchability) or hide it behind `is_succeeded`/etc? Keeping it private (module-level, not exported) is cleaner.
