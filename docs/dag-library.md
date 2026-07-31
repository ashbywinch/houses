# DAG Library — `dag/`

**For:** Developers adding or maintaining DAG nodes in `houses/nodes/`.

The `dag/` library is a **reactive directed acyclic graph**: when a leaf node's value
changes, every downstream node that depends on it automatically recomputes and persists
its new result.

```
UserInputNode ──► DerivedNode ──► DerivedNode
```

Each link is a signal connection — the downstream node is notified when its dependency
changes and schedules itself for refresh.

---

## Core Concepts

### Nodes

Two concrete node types, one abstract base:

| Type | Purpose | Set by |
|---|---|---|
| `UserInputNode[T]` | A leaf whose value is pushed externally | Enrichment modules, API handlers, WebSocket |
| `DerivedNode[T]` | Computed from one or more dependency nodes | Its own `compute()` — never set externally |
| `Node[T]` (abstract) | Shared identity, signal, serialisation, persistence | — |

Every node has:
- **`node_id`** — globally unique string, typically `"{rid}/{node_name}"` (e.g. `"12345/council_tax"`).
- **`value_type`** — the Python type `T`, used by a Pydantic `TypeAdapter` to validate and serialise values.
- **`display_name`** — auto-generated from the node_id stem.

### The three-state result: `Attempt[T]`

Every node's value is an `Attempt` — a discriminated union with three states:

| State | Created via | Meaning |
|---|---|---|
| `succeeded` | `Attempt.succeeded(value)` | Computation finished with a value |
| `pending` | `Attempt.pending()` | Not yet computed, or waiting on dependencies |
| `impossible` | `Attempt.impossible("reason")` | Computation failed irrecoverably |

A `DerivedNode`'s `compute()` must **always** return an `Attempt`. The framework never
calls `compute()` if a dependency is impossible — it short-circuits and propagates
impossible upstream. If any dependency is pending, the node defers without computing.

#### Structured errors: `AttemptError`

Every impossible `Attempt` carries a structured `AttemptError` (via `Attempt.error_info`):
`code`, `message`, `retryable`, `source`, `causes`, and — in memory — the **actual
exception object** (`exc`). Inspect the exception's attributes (`.status`, `.headers`,
`retry_after`) rather than parsing the message string:

```python
# Wrong — string parsing
if "429" in attempt.error:
    retry = True

# Right — structured
retry = attempt.error_info.retryable
status = attempt.error_info.exc.status  # the actual HttpError
```

`Attempt.impossible()` **auto-captures the active exception** when called inside an
`except` block — the traceback and retryability are recorded without extra plumbing.
A plain `Attempt.impossible("reason")` outside an `except` gets `code="no_data"`.

Serialization (in `to_json()`) emits:
- `error` — the **user-facing** message, safe to render in the UI. This is
  `AttemptError.display_message`: the explicit `user_message` if set, else the
  deepest cause's message (the leaf reason), else the internal message. For a
  dep-failure chain the UI therefore sees the friendly leaf text (e.g.
  "Works estimate required for: Ashby"), never node ids or `dep failed`.
- `error_detail` — the structured error (code, retryable, source, `exc_type`,
  traceback, `causes` chain) for debugging. `error_detail.message` keeps the
  full internal chain with node ids. **Internal — never render in the UI.**

`classify_exception()` is the single source of truth for retryability: it maps an
exception to `(code, retryable)` and handles `HttpError` (`.status`), httpx errors
(`.response.status_code`), `TimeoutError`, and everything else. Both `AttemptError`
and the DAG retry machinery use it.

#### Error propagation contract

- **Services that call APIs return `Attempt`.** The failure reason travels in
  `Attempt.error` (message) and `Attempt.error_info` (structured), so the frontend
  can render it and the DAG can act on it. E.g. `epc.lookup_epc` returns
  `Attempt[str]` — never `str | None` or `""` on failure.
- **Services that don't call APIs throw exceptions.** Pure computation has no
  structured result channel; a failure is exceptional and should propagate. The
  framework catches exceptions from `compute()`, records them on the `Attempt`,
  and retries transient ones.

**Either way, the reason must not be lost.**

```python
# Wrong — API service swallows the reason into a bare value
async def lookup(postcode: str) -> str | None:
    try:
        ...
    except Exception:
        return None  # caller can't distinguish "no data" from "API down"

# Right — API service returns Attempt with the reason
async def lookup(postcode: str) -> Attempt[str]:
    try:
        ...
    except httpx.HTTPStatusError:
        raise  # transient — let the DAG retry
    except Exception as e:
        return Attempt.impossible(f"lookup failed: {e}")
```

Transient HTTP errors (429, 5xx, timeouts) are **re-raised** (not caught) so the DAG's
retry machinery schedules a retry. Permanent failures return `Attempt.impossible(reason)`.
The retry decision is driven by `AttemptError.retryable` — a service that catches a
transient error and returns `Attempt.impossible(...)` is retried exactly like one that
re-raises.

#### Nodes propagate, never re-literalize

When a node receives a failed dependency or service `Attempt`, propagate its reason —
don't replace it with a fresh generic string:

```python
# Wrong — invents a literal, hiding the real reason
if not result.succeeded:
    return Attempt.impossible("no data")

# Right — propagate the actual reason
if not result.succeeded:
    return Attempt.impossible(result.error or "no data")
```

`Node._impossible(dep_attempts)` and the DAG's dep-failure path already build
`code=dep_failed` errors with a `causes` chain of the failed dependencies' `error_info`
— the chain is traversable structurally, not by string matching.

### Provenance

Every node tracks *where its value came from* via `build_provenance()`, which walks the
dependency tree and returns a `Provenance` dataclass. Provenance is serialised in every
node's `to_json()` output.

Provenance is fully dynamic — regenerated on every `to_json()` call. There is no
provenance migration; nothing to persist. See *Provenance auto-generation* below.

---

## Reactive Lifecycle

### Creation

```
UserInputNode(...)  ──► loads last persisted value from SQLite
DerivedNode(...)     ──► loads last persisted value
                      ──► connects a Slot to each dep's .changed signal
                      ──► registers with the scheduler
```

### Change propagation

```
push(value)  ──► persist to SQLite
              ──► emit .changed signal → downstream node scheduled
```

### Refresh (scheduler picks it up)

```
Scheduler runs node.refresh():
  1. _is_stale() — compares dependency timestamps vs own computed_at
  2. Gathers each dep's latest Attempt
  3. Any dep impossible? → short-circuit to Attempt.impossible
  4. Any dep pending?   → defer (return, try again later)
  5. Calls compute(*dep_attempts)
       On success: persist, emit .changed (cascading)
       On exception:
         Transient → schedule_retry with exponential backoff
         Permanent → Attempt.impossible
```

Staleness is determined entirely by timestamps. No explicit invalidation calls needed.

### Serialisation

- **`to_json()`** — full output with provenance tree. Use for single-node reads.
- **`to_json_value()`** — lightweight, skips provenance tree. Use for bulk-list endpoints.

---

## Expression System

Instead of writing imperative `compute()` methods, nodes can declare their calculation
as an **expression tree**. The base class handles evaluation and provenance generation
automatically.

### Declaring an expression

Set `expression` as a property that returns an `Expression` tree:

```python
from dag.expression import Ref, Literal, Add, Sub, Conditional
from dag.derived_node import DerivedNode

class MortgageRequiredNode(DerivedNode[Money]):
    @property
    def expression(self):
        return (
            Ref(self._deps[0])       # price
            + Ref(self._deps[1])     # stamp_duty
            + Ref(self._deps[2])     # works
            - Ref(self._deps[3])     # equity
        )

    def compute(self, price, sd, works, equity):
        return self.expression.evaluate()
```

Node objects can be used directly in expressions — they implement `__add__`, `__sub__`,
`__mul__`, `__truediv__`, and `__neg__` that create the corresponding `Expression` tree:

```python
self._price_node + self._stamp_duty_node - self._equity_node
# Creates: Sub(Add(Ref(price), Ref(stamp_duty)), Ref(equity))
```

### Expression types

| Expression | Purpose |
|---|---|
| `Ref(node)` | Reference a dependency node — calls `latest_attempt()` |
| `Literal(value)` | A constant |
| `Add, Sub, Mul, Div, Negate` | Arithmetic on expression trees |
| `PMT(principal, rate, term)` | Monthly mortgage payment formula |
| `Conditional(pred, if_true, if_false)` | Branch based on a zero-arg predicate |
| `TieredRate(value, tiers)` | Marginal tax/rate calculation across bands |
| `Choose(alternatives, selector)` | Pick best from already-computed alternatives |
| `Field(source, key)` | Extract a dict key from a node's value |
| `Attr(source, attr)` | Extract an attribute from a node's value |

### Provenance auto-generation

When a node has an `expression` set, `build_provenance()` calls `expression.to_formula()`
to generate formula lines automatically. The expression tree is walked to produce
labelled formula lines showing every term, its value, and the final result.

Nodes that use expressions **do not override `build_provenance()`**. The base class
default walks active dependencies and calls `expression.to_formula()`.

### TieredRate — marginal calculations

For calculations with rate bands (stamp duty, tax tiers):

```python
TieredRate(
    self._price_node,
    tiers=[
        (0, 250000, 0),
        (250000, 925000, Decimal("0.05")),
        (925000, 1500000, Decimal("0.10")),
        (1500000, None, Decimal("0.12")),
    ],
)
```

Provenance shows every tier with its range and rate, highlighting the active one.

### Choose — selection with provenance

When multiple alternatives are already computed and one must be selected:

```python
Choose(
    alternatives={
        "walk": Ref(walk_node),
        "transit": Ref(transit_node),
        "drive": Ref(drive_node),
    },
    selector=lambda results: min(results, key=lambda k: results[k].value_or_none().duration.magnitude),
)
```

Provenance shows every alternative with its value and a ✓/✗ marker indicating
which was selected.

### Stable dependencies by default

`_get_active_deps()` should return the same set of deps in most cases. Conditional
deps are for shortcutting — skipping computation entirely. If you want to
calculate all alternatives and then pick one, keep deps stable.

For trivial local computation (reading a cached value, geocoding a coordinate),
keeping the dep stable avoids unnecessary conditional complexity:

```python
# Correct: stable deps, early-return in compute when dep isn't needed

```python
# Correct: stable deps, early-return in compute when dep isn't needed
def __init__(self, ..., transit_result, best_location):
    super().__init__(node_id, Commute, (transit_result, best_location))

def compute(self, transit, location):
    if transit.value_or_none().daily_cost.amount > 0:
        return transit  # early return, NR lookup not needed
    return await self._enrich_rail_fare(commute, location)
```

---

## Settings Nodes

Every financial setting has its own `UserInputNode`, created by `Services.__post_init__`
from `SETTING_DEFAULTS` in `houses/nodes/settings_node.py`. Consumer nodes reference
individual setting nodes directly, not a blob:

```python
class YearlySinkingFundNode(DerivedNode[Money]):
    def __init__(self, *, rightmove_price, sinking_fund_rate_node):
        super().__init__(node_id, Money, (rightmove_price, sinking_fund_rate_node))
```

The `SettingsNode` aggregate is available via `svc.settings_view` for API backward
compat, but consumer code never uses it.

Settings are pushed via the PATCH endpoint (`/api/settings/financial`), which pushes
to individual nodes and lets the signal chain propagate.

---

## Writing a Node

### `UserInputNode` — external data source

```python
from dag.user_input_node import UserInputNode

purchase_price = UserInputNode[Money]("12345/purchase_price", Money)
purchase_price.push(Money("350000", "GBP"), source_label="rightmove_scraper")
# Persisted + emitted .changed — downstream DerivedNodes pick it up.
```

### `DerivedNode` — computed from dependencies

```python
from dag.attempt import Attempt
from dag.derived_node import DerivedNode

class StampDutyNode(DerivedNode[Money]):
    def __init__(self, node_id: str, *, rightmove_price, status_node=None):
        self._status_node = status_node
        deps = [rightmove_price]
        if status_node is not None:
            deps.append(status_node)
        super().__init__(node_id, Money, tuple(deps))

    @property
    def expression(self):
        return Conditional(
            predicate=lambda: (
                self._status_node.latest_attempt().value_or_none() or ""
            ).strip().lower() == "current",
            if_true=Literal(Money("0", "GBP")),
            if_false=TieredRate(self._deps[0], tiers=[
                (0, 250000, 0),
                (250000, 925000, Decimal("0.05")),
                (925000, 1500000, Decimal("0.10")),
                (1500000, None, Decimal("0.12")),
            ]),
        )

    def compute(self, price, status=None):
        return self.expression.evaluate()
```

---

## Design Rules

### One concept per node

If `compute()` calculates something that isn't the node's named purpose, split it.
The signal chain then tracks real dependencies correctly, and downstream nodes
can depend on just the value they need.

### Stable dependencies

`_get_active_deps()` must always return the same deps tuple. If a dep is sometimes
unnecessary, keep it in the deps and early-return in `compute()`. This keeps
provenance transparent and prevents staleness tracking bugs.

### No side effects in compute

Derived nodes must not push values into other nodes. Use a dependency chain
instead — the signal cascade handles propagation automatically.

### Values must be typed classes, not dicts

Use frozen dataclasses or Pydantic models so the value type is self-documenting
and the TypeAdapter round-trips safely:

```python
@dataclass(frozen=True)
class CommuteResult:
    duration: Quantity
    daily_cost: Money
    mode: str
```

### Service returns wrapped in Attempt

When a service returns `School | None`, the node wraps it in `Attempt.succeeded(school)`
or `Attempt.impossible("not found")`.

### Conditional deps shortcut work; stable deps calculate then compare

Use conditional deps (`_get_active_deps()`) when you want to **skip computation
entirely** — the dep shouldn't compute at all. `IfThenElseNode` is the canonical
example: when the condition is true, the `else` branch is excluded from deps, so
its entire computation chain never runs.

Don't use conditional deps when you want **all alternatives to compute so you
can compare them**. `CommuteSelectorNode` keeps walk, transit, and drive as
stable deps — they all compute independently, then `Choose` picks the best one.

**Conditional dep — skip work entirely:**
```python
# IfThenElseNode: else branch excluded when condition is true
```

**Stable dep — calculate then decide:**
```python
# CommuteSelectorNode: walk, transit, drive all compute, Choose selects
```


When `compute()` changes in a way that makes old persisted results semantically invalid,
bump the node_id:

```python
# After — old persisted results under "town_desc" are ignored
self.town_desc = TownDescNode(f"{rid}/town_desc_v2", ...)
```

The new node_id has no persisted data, so the node starts as `Attempt.pending()` and
recomputes on next scheduler pass. Old results are orphaned harmlessly.

**When NOT to bump:** cosmetic refactors, adding logging, changing error messages,
or any change producing the same output for the same inputs.

---

## Debugging

### Tracing failures through provenance

Every node's `to_json()` output includes a `provenance` dict. When a node fails:

1. Read the failed node's `node_results` — the `error` field says
   `"dep failed: X failed"` or gives the compute error directly.
2. Read the dep's `node_results` for its provenance label and error.
3. Repeat until you reach the root cause (a source node or an API error).

Do not delete DB rows, clear caches, or restart the server to investigate — that
destroys the evidence. Read the provenance chain instead.

### DB isolation for tests

Tests replace the global DB connection with an in-memory SQLite. Settings sources
live in `Services` (not at module level), so they read from the in-memory DB
automatically — no stale cached data, no real DB access during test collection.

---

## Datetime rules

- Store and process in UTC (`datetime.now(UTC)`, never `datetime.now()`).
- After `datetime.fromisoformat()`, check `dt.tzinfo is None` and replace with UTC.
- Display in the user's local timezone at the presentation boundary.
- When reading from external APIs, document the source's timezone and convert explicitly.
