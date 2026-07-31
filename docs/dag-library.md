# DAG Library — `dag/`

**For:** developers adding/maintaining DAG nodes in `houses/nodes/`.

Reactive directed acyclic graph: when a leaf changes, every downstream node
recomputes and persists. Each link is a signal connection — downstream nodes
schedule themselves when a dependency changes.

```
UserInputNode ──► DerivedNode ──► DerivedNode
```

## Core Concepts

### Nodes

| Type | Purpose | Set by |
|---|---|---|
| `UserInputNode[T]` | Leaf; value pushed externally | Enrichment modules, API handlers, WebSocket |
| `DerivedNode[T]` | Computed from dependencies | Its own `compute()` — never set externally |
| `Node[T]` (abstract) | Identity, signal, serialisation, persistence | — |

Every node: `node_id` (unique, `"{rid}/{node_name}"` e.g. `"12345/council_tax"`), `value_type` (Python type `T`, Pydantic TypeAdapter validates/serialises), `display_name` (from node_id stem).

### The three-state result: `Attempt[T]`

| State | Created via | Meaning |
|---|---|---|
| `succeeded` | `Attempt.succeeded(value)` | Has a value |
| `pending` | `Attempt.pending()` | Not computed / waiting on deps |
| `impossible` | `Attempt.impossible("reason")` | Failed irrecoverably |

`compute()` MUST return an `Attempt`. Framework short-circuits: dep impossible → propagate upstream (never call compute); dep pending → defer.

#### AttemptError — structured errors

`Attempt.error_info` = `AttemptError{code, message, user_message, retryable, source, causes, exc, traceback}`.

**Never parse error strings. Inspect the exception:**

```python
# ✗ string parsing
if "429" in attempt.error:
    retry = True
# ✓ structured
retry = attempt.error_info.retryable
status = attempt.error_info.exc.status   # actual HttpError
```

- `exc` — real exception object (in-memory only; never serialized).
- Auto-captures active exception when `impossible()` is called inside `except`.
- `Attempt.impossible("msg")` outside `except` → `code="no_data"`.

**Field split (UI vs internal):**

| Field | Content | Render in UI? |
|---|---|---|
| `error` | `display_message` — friendly leaf text ("Works estimate required for: Ashby") | ✅ |
| `error_detail.message` | full internal chain, node ids, `dep failed` markers | ❌ debugging only |
| `error_detail.traceback` | captured traceback | ❌ debugging only |

`display_message` resolution: explicit `user_message` → deepest cause's message → `message`. Dep-failure chains surface the leaf reason, never ids.

`classify_exception(exc) → (code, retryable)` is the ONE source of truth for retryability — used by both `AttemptError` and the DAG retry machinery. Handles `HttpError.status`, `httpx` `.response.status_code`, `TimeoutError`.

#### Error propagation contract

| Service type | Failure channel | Transient errors |
|---|---|---|
| Calls APIs | return `Attempt[...]` | re-raise (`httpx.HTTPStatusError`, `RequestError`, timeout) → DAG retries |
| Pure computation | throw | n/a — framework catches, records on Attempt |

**Never lose the reason.** ✗ `return None` / `return ""` on failure (caller can't distinguish "no data" from "API down"); ✓ `Attempt.impossible(reason)`.

```python
# ✗ swallows the reason
async def lookup(pc: str) -> str | None:
    try: ...
    except Exception:
        return None
# ✓ propagates it
async def lookup(pc: str) -> Attempt[str]:
    try: ...
    except httpx.HTTPStatusError:
        raise                       # transient → DAG retries
    except Exception as e:
        return Attempt.impossible(f"lookup failed: {e}")
```

Retry decision = `AttemptError.retryable`, regardless of raise-vs-return style: a service that catches a transient error and returns `impossible(...)` is retried exactly like one that re-raises.

#### Nodes: propagate, never re-literalize

**Never** replace a failed dep/service reason with a fresh string:

```python
# ✗ invents a literal, hides the real reason
if not result.succeeded:
    return Attempt.impossible("no data")
# ✓ propagate
if not result.succeeded:
    return Attempt.impossible(result.error or "no data")
```

`Node._impossible(deps)` and the dep-failure path build `code=dep_failed` with a `causes` chain of each failed dep's `error_info` — traverse structurally, never by string match.

### Provenance

Every node tracks *where its value came from* via `build_provenance()`, which walks the dependency tree and returns a `Provenance` dataclass, serialised in every node's `to_json()` output.

Fully dynamic — regenerated on every `to_json()` call. No provenance migration; nothing to persist. See *Provenance auto-generation*.

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

Staleness determined entirely by timestamps. No explicit invalidation calls needed.

### Serialisation

- **`to_json()`** — full output with provenance tree. Use for single-node reads.
- **`to_json_value()`** — lightweight, skips provenance tree. Use for bulk-list endpoints.

---

## Expression System

Nodes can declare their calculation as an **expression tree** instead of an imperative `compute()`. The base class handles evaluation and provenance generation automatically.

### Declaring an expression

Set `expression` as a property returning an `Expression` tree:

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

Node objects work directly in expressions — they implement `__add__`, `__sub__`, `__mul__`, `__truediv__`, `__neg__` that build the corresponding tree:

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

With `expression` set, `build_provenance()` calls `expression.to_formula()` to generate labelled formula lines (every term, its value, the result). Expression-based nodes **do not override `build_provenance()`** — the base default walks active deps and calls `expression.to_formula()`.

### TieredRate — marginal calculations

Rate bands (stamp duty, tax tiers):

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

Alternatives already computed; one selected:

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

Provenance shows every alternative with its value and a ✓/✗ marker indicating which was selected.

### Stable dependencies by default

`_get_active_deps()` should return the same deps in most cases. **Conditional deps are for shortcutting — skipping computation entirely.** If you want all alternatives computed then compared, keep deps stable. For trivial local computation (reading a cached value, geocoding a coordinate), stable deps avoid unnecessary conditional complexity:

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

Every financial setting has its own `UserInputNode`, created by `Services.__post_init__` from `SETTING_DEFAULTS` in `houses/nodes/settings_node.py`. Consumer nodes reference individual setting nodes directly, not a blob:

```python
class YearlySinkingFundNode(DerivedNode[Money]):
    def __init__(self, *, rightmove_price, sinking_fund_rate_node):
        super().__init__(node_id, Money, (rightmove_price, sinking_fund_rate_node))
```

`SettingsNode` aggregate is available via `svc.settings_view` for API backward compat, but consumer code never uses it.

Settings are pushed via PATCH `/api/settings/financial`, which pushes to individual nodes and lets the signal chain propagate.

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

| Rule | Detail |
|---|---|
| **One concept per node** | If `compute()` does something beyond its named purpose, split. Signal chain then tracks real deps; downstream nodes depend on just what they need. |
| **Stable dependencies** | `_get_active_deps()` must always return the same deps tuple. If a dep is sometimes unnecessary, keep it in deps and early-return in `compute()`. Keeps provenance transparent, prevents staleness bugs. |
| **No side effects in compute** | Derived nodes must not push into other nodes. Use a dependency chain — the signal cascade handles propagation. |
| **Typed values, not dicts** | Frozen dataclasses or Pydantic models so the value type is self-documenting and the TypeAdapter round-trips safely: |
| **Service results wrapped in Attempt** | `School | None` → node wraps in `Attempt.succeeded(school)` or `Attempt.impossible("not found")` |
| **Conditional deps skip; stable deps compare** | Conditional (`_get_active_deps()`) skips computation entirely — `IfThenElseNode` excludes the else branch so its chain never runs. Stable deps when all alternatives must compute then compare — `CommuteSelectorNode` keeps walk/transit/drive stable, `Choose` picks best. |

```python
@dataclass(frozen=True)
class CommuteResult:
    duration: Quantity
    daily_cost: Money
    mode: str
```

### Bumping node_id

When `compute()` changes such that old persisted results are semantically invalid, bump the node_id:

```python
# After — old persisted results under "town_desc" are ignored
self.town_desc = TownDescNode(f"{rid}/town_desc_v2", ...)
```

New node_id has no persisted data → starts `Attempt.pending()` and recomputes on next scheduler pass. Old results orphan harmlessly.

**When NOT to bump:** cosmetic refactors, adding logging, changing error messages, or any change producing the same output for the same inputs.

---

## Debugging

### Tracing failures through provenance

Every node's `to_json()` includes a `provenance` dict. On failure:

1. Read the failed node's `node_results` — `error` says `"dep failed: X failed"` or the compute error directly.
2. Read the dep's `node_results` for its provenance label and error.
3. Repeat until the root cause (source node or API error).

**Never delete DB rows, clear caches, or restart the server to investigate** — that destroys the evidence. Read the provenance chain instead.

### DB isolation for tests

Tests replace the global DB connection with in-memory SQLite. Settings sources live in `Services` (not module level), so they read the in-memory DB automatically — no stale cached data, no real DB access during test collection.

---

## Datetime rules

- Store/process in UTC (`datetime.now(UTC)`, never `datetime.now()`).
- After `datetime.fromisoformat()`, check `dt.tzinfo is None` and replace with UTC.
- Display in the user's local timezone at the presentation boundary.
- External APIs: document the source's timezone and convert explicitly.
