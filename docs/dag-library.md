# DAG Library — `dag/`

**For:** Developers adding or maintaining DAG nodes in `houses/nodes/`.

The `dag/` library is a **reactive directed acyclic graph**: when a leaf node's value
changes (an address is corrected, a commute threshold updated), every downstream node
that depends on it automatically recomputes and persists its new value.

```
UserInputNode ──(changed signal)──► DerivedNode ──(changed signal)──► DerivedNode
```

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
- **`display_name`** — auto-generated from the node_id stem (`"council_tax"` → `"Council Tax"`).

### The three-state result: `Attempt[T]`

Every node's value is an `Attempt` — not an `Optional`, not an exception, not a raw value.
It is a discriminated union with three states:

| State | Created via | Meaning |
|---|---|---|
| `succeeded` | `Attempt.succeeded(value)` | Computation finished with a value |
| `pending` | `Attempt.pending()` | Not yet computed, or waiting on dependencies |
| `impossible` | `Attempt.impossible("reason")` | Computation failed irrecoverably |

A `DerivedNode`'s `compute()` must **always** return an `Attempt`. The framework never
calls `compute()` if a dependency is impossible — it short-circuits and propagates
impossible upstream. If any dependency is pending, the node defers without computing.

### Provenance

Every node tracks *where its value came from* via `build_provenance()`, which walks the
dependency tree and returns a `Provenance` dataclass. Provenance is serialised in every
node's `to_json()` output as `{"label": "Council Tax", "sourceType": "api", "sources": {...}}`.

Use provenance to trace failures: see *Tracing failures* below.

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
              ──► emit .changed signal
                    │
                    ▼
            DerivedNode._on_dep_changed()
              ──► scheduler.schedule()   (node is now pending)
```

### Refresh (scheduler picks it up)

```
Scheduler runs node.refresh():
  1. _is_stale() — compares dependency timestamps vs own computed_at
  2. Gathers each dep's latest Attempt
  3. Any dep impossible? → short-circuit to Attempt.impossible
  4. Any dep pending?   → defer (return, try again later)
  5. Calls compute(*dep_attempts)   ──► may be sync or async
       On success: persist, emit .changed (cascading), call after_refresh hook
       On exception:
         Transient (TimeoutError, HTTP 429/5xx) → schedule_retry with exponential backoff
         Permanent                              → Attempt.impossible
```

Staleness is determined entirely by timestamps. A node is stale when a dependency's
`_db_created_at` or `_computed_at` is newer than the node's own `_computed_at`. No
explicit invalidation calls needed.

### Serialisation

- **`to_json()`** — full output with provenance tree (expensive). Use for single-node reads.
- **`to_json_value()`** — lightweight, skips provenance tree. Use for bulk-list endpoints.

---

## Writing a Node

### `UserInputNode` — external data source

```python
from dag.user_input_node import UserInputNode

purchase_price = UserInputNode[Money]("12345/purchase_price", Money)
purchase_price.push(Money("350000", "GBP"), source_label="rightmove_scraper")
# Persisted + emitted .changed — downstream DerivedNodes pick it up.
```

The constructor loads the last persisted value from SQLite automatically. Calling
`push()` overwrites it, persists, and emits the `changed` signal.

### `DerivedNode` — computed from dependencies

```python
from dag.attempt import Attempt
from dag.derived_node import DerivedNode

class StampDutyNode(DerivedNode[Money]):
    def __init__(self, node_id: str, price_node: UserInputNode[Money]):
        super().__init__(node_id, Money, deps=(price_node,))

    def compute(self, price: Attempt[Money]) -> Attempt[Money]:
        if not price.succeeded:
            return Attempt.impossible("purchase price not available")
        p = price.value_or_none()
        if p < Money("250000", "GBP"):
            return Attempt.succeeded(Money("0", "GBP"))
        return Attempt.succeeded(p * Decimal("0.05"))
```

The `deps` tuple pins which nodes this depends on. `compute()` receives each dep's
`Attempt` in the same order. It can be sync (as above) or `async`.

### `IfThenElseNode` — conditional branches

For nodes that activate different dependency chains based on a condition:

```python
from dag.if_then_else import IfThenElseNode

commute_cost = IfThenElseNode(
    node_id=f"{rid}/commute_cost",
    value_type=Money | None,
    condition_sources=(needs_rail_fare_node,),
    condition_fn=lambda needs_rail: needs_rail.succeeded and needs_rail.value_or(False),
    then_branch=rail_fare_node,
    else_branch=driving_cost_node,
)
```

The predicate receives `Attempt` values for each condition source. Only the active
branch's dependencies are tracked for staleness. If no branch activates, returns
`Attempt.succeeded(None)` (type must be nullable).

---

## Design Rules

### One concept per node

If `compute()` calculates something that isn't the node's named purpose, split it.
The signal chain then tracks real dependencies correctly, and downstream nodes
can depend on just the value they need.

**Signal to split:** You're adding code that reads a dep the node didn't already use,
or returning a value conceptually independent of the node's return type.

### No side effects in compute

Derived nodes must not push values into other nodes. Use a dependency chain
instead — the signal cascade handles propagation automatically:

1. School node computes → emits `changed`
2. SchoolPostcodeNode (depends on SchoolNode) becomes stale → recomputes → emits `changed`
3. TransitNode (depends on SchoolPostcodeNode) becomes stale → recomputes

No manual `_sync_` methods needed.

### Values must be typed classes, not dicts

Use frozen dataclasses or Pydantic models so the value type is self-documenting
and the TypeAdapter round-trips safely:

```python
# ✅
@dataclass(frozen=True)
class CommuteResult:
    duration: Quantity
    daily_cost: Money
    mode: str

# ❌ — no schema, fields can drift
{"duration": 32, "daily_cost": 4.50}
```

### Service returns wrapped in Attempt

When a service returns `School | None`, the node wraps it in `Attempt.succeeded(school)`
or `Attempt.impossible("not found")`. The DAG boundary always uses `Attempt` — never
pass raw return values between nodes.

### Settings sources live in Services, not module-level

Settings nodes (`persons_source`, `financial_source`, `commute_thresholds_source`) live
in the `Services` DI container, not as module variables. Access them through the
container:

```python
from houses.context import get_services
svc = get_services()
data = await svc.persons_source.attempt()
```

Updating settings uses the PATCH endpoint, which pushes into the `UserInputNode` and
lets the signal chain propagate changes to all downstream nodes:

```bash
curl -X PATCH http://localhost:8080/api/settings/persons \
  -H 'Content-Type: application/json' \
  -d '[...]'
```

Never delete the database to force a recompute — use the PATCH endpoint.

### Node versioning

When `compute()` changes in a way that makes old persisted results semantically invalid
(different inputs, new dependencies, changed algorithm), bump the node_id:

```python
# Before
self.town_desc = TownDescNode(f"{rid}/town_desc", ...)

# After — old persisted results under "town_desc" are ignored
self.town_desc = TownDescNode(f"{rid}/town_desc_v2", ...)
```

The new node_id has no persisted data, so:
1. Node starts as `Attempt.pending()` on next reload
2. Scheduler processes it: compute runs with current deps
3. Result persisted under the new node_id
4. Old results are orphaned harmlessly

No DB surgery, no manual clearing. The server reloads .py changes automatically.

**When NOT to bump:** cosmetic refactors, adding logging, changing error messages,
or any change producing the same output for the same inputs.

---

## Debugging

### Tracing failures through provenance

Every node's `to_json()` output includes a `provenance` dict with a `label`. When a
node fails:

1. Read the failed node's `node_results` — the `error` field says
   `"dep failed: X failed"` or gives the compute error directly.
2. Read the dep's `node_results` for its provenance label and error.
3. Repeat until you reach the root cause (a source node or an API error).

Do not delete DB rows, clear caches, or restart the server to investigate — that
destroys the evidence. Read the provenance chain instead.

### DB isolation for tests

Tests replace the global DB connection with an in-memory SQLite. Because settings
sources live in Services (not at module level), they read from the in-memory DB
automatically — no stale cached data, no real DB access during test collection.
See `tests/unit/conftest.py`.
