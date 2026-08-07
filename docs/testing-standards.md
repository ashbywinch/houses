# Testing Standards — Houses

Test conventions for the property enrichment engine. Supplementary to `docs/coding-standards.md` — read both.

## Organization

Test files mirror the module under test (`houses/nodes/area.py` → `tests/unit/nodes/test_area.py`); functions describe behaviour (`test_walk_selected_when_fastest`, `test_empty_comment_rejected`); classes group scenarios per method/state. `tests/helpers.py` holds fakes; `tests/unit/isolation_fixtures.py` holds DB isolation.

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

`tests/helpers.py` provides a fake per service protocol, with constructor overrides controlling returned data; `make_services()` builds a `Services` with all fakes at sensible defaults, overridable by keyword:

```python
from tests.helpers import make_services, FakeEPC, FakeCommuteRouter

services = make_services(
    epc_service=FakeEPC(band="B"),
    commute_router=FakeCommuteRouter(simon=42, lorena=55),
)
```

No-arg `make_services()` = working environment for most tests. Default behaviours are visible in the fake constructors.

## Assertions

- Assert **behaviour**, not implementation — return value or side effect, not which internal method was called.
- `assert` over `self.assert*`.
- `pytest.raises` for errors; verify the message when it's part of the contract.
- `in` checks for partial error-message matches, not hardcoded full strings.

## Regression tests assert the contract, not the fix

A test written for a bug fix asserts the user-visible invariant the bug violated — the outcome a caller relies on — never the mechanism you changed:

- **Derived from the symptom, not the code.** If writing the test requires the buggy code open, it's testing the mechanism. The bug report states the contract.
- **Mechanism-independent.** It must fail today, pass after the fix, and still fail if the same symptom returns through a different root cause.
- **Interaction-spanning.** Where two things can shadow or override each other (literal vs parameterised routes, default vs specific, toggle states), assert both sides — a precedence bug in either direction must fail loudly.
- **Outcome-level.** "The commute is available on the card" over "the node returned succeeded"; "both routes resolve to their own handlers" over "route X is declared before route Y".

A regression test that only passes because of the specific lines changed is testing the edit, not the bug.

### A regression test declares itself

Nothing in a test's code says it is a regression test, or what the user saw — the intent must be written where reviewers read. Every regression test carries both in its docstring, opening with the symptom:

```python
def test_x_...():
    """Regression: <what the user saw, in user language> — ...
    Contract: <the invariant the bug violated — what must be true>"""
```

- The **function name** states the *contract* (the outcome), never the mechanism.
- The **docstring's first line names the symptom** — the bug report's words ("the dropdown was empty", "the commute disappeared"), not the root cause.
- The root cause may follow, one line, for archaeology — it explains the fix, it does not define the test.

A docstring that only describes the scenario without the symptom is an ordinary behaviour test, and the reviewer cannot tell it pins a real bug.

## Deterministic fixtures

Integration tests needing a real SQLite DB use in-memory, shared between the app and DAG connection paths — see `tests/unit/isolation_fixtures.py`.

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
