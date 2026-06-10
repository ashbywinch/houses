# Plan: Generic `Attempt[T]` Type

## Summary

Create a generic `Attempt[T]` type that distinguishes three states:

| State | Meaning | Example (geocoding) | Fields |
|---|---|---|---|
| `Succeeded` | We have the value | `(51.5, -0.13)` | `value: T`, `source: str` |
| `Pending` | Haven't tried yet | not yet geocoded | — |
| `Impossible` | Tried, can't be obtained | address doesn't exist | `source: str`, `reason: str`, `exception: BaseException \| None` |

`source` records *which resolver* produced the result (e.g. `"postcodes.io"`, `"Google Maps"`, `"nominatim"`), always populated on both Succeeded and Impossible.

## Approach

`Attempt[T]` is a frozen dataclass with a private status enum. No third-party dependencies — stdlib only. Methods for construction, querying, transformation, and exhaustive `match`.

### Key Methods

| Method | Signature | What it does |
|--------|-----------|-------------|
| `succeeded(v, src)` | static → `Attempt[T]` | Construct a success with value and source name |
| `pending()` | static → `Attempt[T]` | Construct an "untried" state |
| `impossible(src, reason, exc=None)` | static → `Attempt[T]` | Construct a terminal failure |
| `is_succeeded` | property → `bool` | Check if this state is Succeeded |
| `is_pending` | property → `bool` | Check if this state is Pending |
| `is_impossible` | property → `bool` | Check if this state is Impossible |
| `get()` | → `T` | Unwrap the value (raises if not Succeeded) |
| `value_or(default)` | → `T \| default` | Unwrap or return default |
| `value_or_none()` | → `T \| None` | Bridge to `Optional` for sheet boundary |
| `map(fn)` | → `Attempt[U]` | Transform the value if Succeeded; pass through Pending/Impossible unchanged |
| `bind(fn)` | → `Attempt[U]` | Chain a fallible transform; `fn` returns `Attempt[U]` |
| `match(succeeded, pending, impossible)` | → `R` | Exhaustive branching — each callback returns the same type `R` |

**`R` explained**: `match` takes three callbacks (one per state) and returns whatever `R` those callbacks return. All three must agree on `R`. This forces the caller to handle every state explicitly — no silent `None` propagation. For example, if the succeeded callback returns `str` and pending returns `str`, then `R` is `str`.

**`map` explained**: Transforms the value *inside* a success. If `Attempt.Succeeded(5)`, then `.map(lambda x: x * 2)` returns `Attempt.Succeeded(10)`. If the attempt is Pending or Impossible, `map` returns the same instance unchanged — no need to check state first.

**`bind` explained**: Like `map` but the transform itself returns an `Attempt`. Use it to chain fallible operations: `.bind(lambda coords: geocode(coords))` where `geocode` returns `Attempt[...]`. Without bind you'd get `Attempt[Attempt[...]]` (double-wrapped).

### Exhaustion flags are separate

The existing `_APIState` pattern for rate-limit exhaustion is intentionally **not** replaced by `Attempt`. Exhaustion is process-level state that persists across calls (once exhausted, every subsequent call is impossible). Attempts are per-call results. Keep `_APIState` as-is.

## Implementation Phases

### Phase 1 — `houses/attempt.py` + tests

- Frozen `@dataclass` with `_status`, `_value`, `_source`, `_reason`, `_exception`
- `AttemptStatus` enum (private, not exported)
- Static constructors, predicates, extraction, `map`, `bind`, `match`
- New file: `tests/unit/test_attempt.py` — full coverage

No existing code touched. Safe to commit independently.

### Phase 2 — Pilot: `_geocode` return type → `Attempt`

Narrowest possible change. Only `_geocode` (the simplest — takes just a postcode):

- Change return type of `_geocode` from `tuple[float, float] | None` to `Attempt[tuple[float, float]]`
- Use `Attempt.impossible(...)` for all the existing `return None` paths
- Use `Attempt.succeeded(coords, "postcodes.io")` for the success path
- Update the few direct callers in `enricher.py` that consume `_geocode` — they use `.value_or_none()` at the call site to keep downstream code unchanged
- Rope rename to make `_geocode` → `geocode` (now that it returns `Attempt`, it should be public)

### Phase 3 — `_geocode_address`, `_geocode_nominatim` → Attempt

Same pattern, one function per step. Each is a small, testable PR.

### Phase 4 — Caller cleanup: `match` over `value_or_none`

Once all geocoding returns `Attempt`, update the fallback chains in `enricher.py` and `server.py` to use `match` instead of `if coords is None`. This is where the readability gain happens.

### Phase 5 — Transit enrichment (future)

`compute_transit` return type → `Attempt[TransitInfo]`. Only after geocoding is stable.

### Sheet model boundary

`EnrichedProperty` fields stay as `Optional` — convert at the server boundary with `.value_or_none()`. No Pydantic changes needed.

## Files to Create

| File | Purpose |
|---|---|
| `houses/attempt.py` | Generic type definition |
| `tests/unit/test_attempt.py` | Unit tests for attempt type |

## Files to Modify (by phase)

| Phase | File | Changes |
|---|---|---|
| 1 | (none) | New `houses/attempt.py` + tests only |
| 2 | `houses/enricher.py` | `_geocode` return type → `Attempt`, rename to `geocode` |
| 2 | `houses/enricher.py` | Direct callers of `geocode` use `.value_or_none()` |
| 3 | `houses/enricher.py` | `_geocode_address`, `_geocode_nominatim` → `Attempt` |
| 3 | `houses/walkability.py` | `_geocode_town` return type → `Attempt` |
| 4 | `houses/enricher.py` | Fallback chains → `match` instead of `if x is None` |
| 4 | `houses/server.py` | Fallback chains → `match` instead of `if x is None` |
| 5+ | Various | Transit, schools, council tax, EPC — future |
