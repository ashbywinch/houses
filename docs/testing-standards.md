# Testing Standards — Houses

Test conventions for the property enrichment engine. Supplementary to `docs/coding-standards.md` — read both.

## Organization

```
tests/
├── unit/                  # One function/module in isolation
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

**Naming:** files mirror the module under test (`houses/nodes/area.py` → `tests/unit/nodes/test_area.py`); functions describe behaviour (`test_walk_selected_when_fastest`, `test_empty_comment_rejected`); classes group scenarios per method/state.

## Deterministic tests

Same result every run, any order, any machine:

- **No wall-clock time** — `freezegun` or pass timestamps as parameters.
- **No external APIs** — `Services` fakes or `_kwarg` injection.
- **No execution-order dependence** — each test sets up its own data, tears down state. Session-scoped fixtures only for genuinely stateless objects (e.g. schema definitions).
- **Randomised data** — seeded generator.

## No monkeypatching

**Never use `monkeypatch`, `unittest.mock.patch`, or `MockTransport` in new tests.** They patch module/global state → brittle against import-path changes and refactors.

Use one of three DI approaches (from `docs/coding-standards.md`):

| Approach | When |
|----------|------|
| **`_kwarg` injection** | Leaf function needs a pre-built object (registry, HTML fixture) |
| **`Services` container** | Full pipeline needs fake services (EPC, commute, council tax) |
| **`ContextVar`** | Per-request state needs overriding (bus fares, geo state) |

If something isn't reachable through DI, refactor the code to accept a dependency — don't add another patch.

## Fakes & helpers

### `tests/helpers.py`

Reusable fakes for every service protocol; constructor overrides control returned data:

```python
class FakeEPC:
    def __init__(self, band: str = "C", potential: str = "B"):
        self._band = band
        self._potential = potential

    async def lookup(self, postcode: str) -> EPCResult:
        return EPCResult(band=self._band, potential_rating=self._potential)
```

### `make_services()`

Builds a `Services` with all fakes at sensible defaults; override by keyword:

```python
from tests.helpers import make_services, FakeEPC, FakeCommuteRouter

services = make_services(
    epc_service=FakeEPC(band="B"),
    commute_router=FakeCommuteRouter(simon=42, lorena=55),
)
```

No-arg `make_services()` = working environment for most tests.

### Default fake behaviour

| Fake | Default |
|------|---------|
| `FakeGeocoder` | postcode → `(51.5, -0.13)`, outcode → `None` |
| `FakeCommuteRouter` | Simon 45min, Lorena 50min, Petrol 30min |
| `FakeEPC` | Band C, potential B |
| `FakeCouncilTax` | Band D, £1800/yr |
| `FakeWalkability` | Town walk 15min, amenities "shops, park" |
| `FakeTownDesc` | "A suburban area with good transport links." |
| `FakeSchoolLookup` | `None` for all lookups (no schools) |
| `FakeRailFare` | passes `simon`/`lorena` fares through unchanged |

## Assertions

- Assert **behaviour**, not implementation — return value or side effect, not which internal method was called.
- `assert` over `self.assert*`.
- `pytest.raises` for errors; verify the message when it's part of the contract.
- `in` checks for partial error-message matches, not hardcoded full strings.

## Deterministic fixtures

Integration tests needing a real SQLite DB use in-memory:

```python
from houses.database import get_connection
from dag.persistence import _get_db

# App + DAG layers share one in-memory DB in tests — see tests/unit/isolation_fixtures.py
```

## Test smells to avoid

| Smell | Why wrong | Fix |
|-------|-----------|-----|
| `time.sleep()` | flaky, slow | `asyncio.wait_for` or fake the delay |
| reads env var | non-hermetic | inject via parameter/fixture |
| shares mutable state across tests | order-dependent failures | fresh data per test |
| asserts full JSON response string | breaks on any formatting change | assert specific fields |
| requires internet | can't run offline | `Services` fakes |
| fakes the behaviour under test | always passes — fake is a fantasy of the real code | real implementation for code under test; fake only its dependencies (APIs, DBs, services) |
| asserts implementation detail | breaks on refactors that don't change behaviour | assert return values, caller-visible state, or relied-upon side effects — never which private method was called, or in what order |
