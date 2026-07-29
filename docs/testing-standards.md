# Testing Standards — Houses

Test conventions for the property enrichment engine. Supplementary to
`docs/coding-standards.md` — read both.

## Test Organization

```
tests/
├── unit/                  # One function or module in isolation
│   ├── test_attempt.py
│   ├── test_auth.py
│   ├── dag/
│   │   └── test_architecture.py
│   └── nodes/
│       ├── test_commute.py
│       └── test_api.py
├── integration/           # Full pipeline with fake services
│   └── conftest.py
└── conftest.py            # Session-scoped fixtures
```

### Naming

- **Test files** mirror the module under test: `houses/nodes/area.py` →
  `tests/unit/nodes/test_area.py`.
- **Test functions** describe the behaviour: `test_walk_selected_when_fastest`,
  `test_empty_comment_rejected`.
- **Test classes** group related scenarios per method or state.

## Deterministic Tests

Every test must produce the same result on every run, in any order, on any
machine.

- No dependence on wall-clock time. Use `freezegun` or pass timestamps as
  parameters where time matters.
- No dependence on external APIs. Use `Services` fakes or `_kwarg`
  injection.
- No dependence on test execution order. Each test sets up its own data and
  tears down state. Session-scoped fixtures are acceptable only for objects
  that are genuinely stateless (e.g. schema definitions).
- Randomised data must use a seeded generator.

## No Monkeypatching

**Never use `monkeypatch`, `unittest.mock.patch`, or `MockTransport` in new
tests.** These approaches patch module-level or global state, making tests
brittle against import path changes and refactoring.

Instead, use one of the three DI-based approaches from `docs/coding-standards.md`:

| Approach | When |
|----------|------|
| **`_kwarg` injection** | Leaf-level function needs a pre-built object (registry, HTML fixture) |
| **`Services` container** | Full enrichment pipeline needs fake services (EPC, commute, council tax) |
| **`ContextVar`** | Per-request state needs overriding (bus fares, geo state) |

If you need to patch something that isn't reachable through these DI
approaches, refactor the code to accept a dependency rather than adding
another patch.

## Fakes and Helpers

### `tests/helpers.py`

Reusable fake implementations of every service protocol. Each fake
accepts constructor overrides for the data it returns:

```python
class FakeEPC:
    def __init__(self, band: str = "C", potential: str = "B"):
        self._band = band
        self._potential = potential

    async def lookup(self, postcode: str) -> EPCResult:
        return EPCResult(band=self._band, potential_rating=self._potential)
```

### `make_services()`

Build a `Services` container with all fakes at sensible defaults. Override
individual services by keyword:

```python
from tests.helpers import make_services, FakeEPC, FakeCommuteRouter

services = make_services(
    epc_service=FakeEPC(band="B"),
    commute_router=FakeCommuteRouter(simon=42, lorena=55),
)
```

The defaults are chosen so that `make_services()` with no arguments
produces a working environment for most tests.

### What Each Fake Returns by Default

| Fake | Default behaviour |
|------|-------------------|
| `FakeGeocoder` | Returns `(51.5, -0.13)` for postcode, `None` for outcode |
| `FakeCommuteRouter` | Simon=45min, Lorena=50min, Petrol=30min |
| `FakeEPC` | Band C, potential B |
| `FakeCouncilTax` | Band D, £1800/yr |
| `FakeWalkability` | Town walk 15min, amenities "shops, park" |
| `FakeTownDesc` | Returns "A suburban area with good transport links." |
| `FakeSchoolLookup` | Returns `None` for all lookups (no schools found) |
| `FakeRailFare` | Passes `simon`/`lorena` fares through unchanged |

## Assertions

- Assert the **behaviour**, not the implementation. Test the return value
  or side effect, not which internal method was called.
- Prefer `assert` over `self.assert*` in pytest-style tests.
- Use `pytest.raises` for error cases. Verify the error message when it's
  part of the contract.
- Use `in` checks for partial string matches in error messages, not
  hardcoded full strings.

## Deterministic Fixtures

Integration tests that need a real SQLite database use an in-memory
database:

```python
from houses.database import get_connection
from dag.persistence import _get_db

# Both application and DAG layers share the same in-memory DB
# in tests — see tests/unit/isolation_fixtures.py
```

## Test Smells to Avoid

| Smell | Why it's wrong | Fix |
|-------|----------------|-----|
| Test calls `time.sleep()` | Flaky, slow | Use `asyncio.wait_for` or fake the delay |
| Test reads from an environment variable | Non-hermetic | Inject the value via a parameter or fixture |
| Test shares mutable state with other tests | Order-dependent failures | Create fresh data per test |
| Test asserts a full JSON response string | Brittle — breaks on any formatting change | Assert on specific fields |
| Test requires internet access | Can't run offline | Use `Services` fakes |
| Test fakes the behaviour under test | Always passes, never catches regressions — the fake implements a fantasy version of the real code | Use real implementations for the code under test; fake only its *dependencies* (APIs, databases, services) |
| Test asserts implementation detail instead of behaviour | Breaks on refactoring that doesn't change observable behaviour — tests become a liability, not a safety net | Assert on return values, state changes visible to callers, or side effects the caller relies on. Never assert on which private method was called or in what order |
