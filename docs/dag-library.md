# DAG Library — `dag/`

**Audience:** Developers and agents implementing or extending DAG nodes in `houses/nodes/`.
**Purpose:** Document the `dag/` library's architecture, key types, lifecycle, and design patterns.

The `dag/` directory is a **generic reactive directed acyclic graph** library.
Property data is managed as a graph of interconnected nodes: when a leaf node changes
(an address is corrected, a commute threshold updated), downstream computed nodes
are automatically re-evaluated and their new values persisted.

```
UserInputNode ──(changed signal)──► DerivedNode ──(changed signal)──► DerivedNode
```

---

## Core Types

### `Attempt[T]` — Three-state result wrapper

Every node resolves to an `Attempt`. It is **not** an exception or optional — it is
a discriminated union with three states:

| State | Created via | Meaning |
|---|---|---|
| `succeeded` | `Attempt.succeeded(value)` | Computation completed with a value |
| `pending` | `Attempt.pending()` | Not yet computed or waiting on dependencies |
| `impossible` | `Attempt.impossible("reason")` | Computation failed irrecoverably |

```python
from dag.attempt import Attempt

# Construction
success = Attempt.succeeded(42)
waiting = Attempt.pending()
failed  = Attempt.impossible("API returned 500")

# Inspection — instance properties
success.succeeded   # True
waiting.pending     # True
failed.impossible   # True

# Safe extraction
success.value_or_none()      # 42
success.value_or("fallback") # 42
failed.value_or("fallback")  # "fallback"

# Exhaustive matching
msg = attempt.match(
    on_succeeded=lambda v: f"Got {v}",
    on_pending=lambda:      "Still waiting",
    on_impossible=lambda e: f"Failed: {e}",
)

# Functional transforms
attempt.map(lambda v: v * 2)       # transform value if succeeded
attempt.bind(lambda v: fetch(v))   # chain to another Attempt
```

**Contract:** Every `compute()` code path MUST return an `Attempt` — no raw values,
no `None`, no implicit returns.

### `Provenance` — Origin tracking

Every node tracks where its value came from via `build_provenance()`, which walks
the dependency tree and returns a `Provenance` dataclass. Provenance is serialised
in `to_json()` output and is the primary debugging tool for DAG failures (see
*Tracing failures* in `dag-model.md`).

```python
from dag.attempt import Provenance, SourceType

# Simple leaf provenance
Provenance.from_label("Rightmove scraper")

# Composite with sub-sources
Provenance.composite(
    label="Best address",
    sources={
        "rightmove_address": rightmove_provenance,
        "corrected_address": correction_provenance,
    },
)

# Full form
Provenance(
    label="Council tax",
    url="https://voa.gov.uk/...",
    source_type=SourceType.API,
    freshness=datetime.now(UTC),
)
```

`SourceType` enum: `API`, `CALC`, `USER`, `CONFIG`, `GEOCODE`, `DB`.

### `Node[T]` — Abstract base class

`Node` is the abstract base for all DAG nodes. It provides:

- **Identity:** Immutable `node_id` (e.g. `"12345/council_tax"`). The convention is `{rid}/{node_name}`.
- **Value type:** `value_type` — the Python type `T` (used by Pydantic for serialisation round-trips).
- **Signal:** `.changed` — a `Signal` that fires when the node's value changes.
- **Serialisation:** `.to_json()` and `.to_json_value()` return JSON-safe dicts with status, value, provenance.
- **Persistence:** `_load_attempt_from_db()` and `_persist()` load/save via the persistence layer.
- **Display name:** Auto-generated from `node_id` — `"council_tax"` → `"Council Tax"`.

```python
from dag.node import Node

# Subclasses must implement:
#   attempt(self) -> Attempt[T]           — current value
#   build_provenance(self) -> Provenance   — provenance tree
```

### `UserInputNode[T]` — Leaf node set externally

A node whose value is pushed from outside (enrichment modules, API calls,
WebSocket messages). The simplest node type.

```python
from dag.user_input_node import UserInputNode

node = UserInputNode[str]("12345/corrected_address", str)
node.push("123 High Street, London", source_label="user_correction")
# Automatically persists to SQLite and emits `changed` signal.
```

- `push(value, source_label)` sets the value, persists, and emits `changed`.
- Automatically restores the last persisted value from SQLite on construction.
- Validates that property node IDs have numeric RID prefixes (guards against test data
  leaking into production).

### `DerivedNode[T]` — Node computed from dependencies

The workhorse. A `DerivedNode` subscribes to its dependency nodes' `changed` signals
and re-computes its value when any dependency changes.

```python
from dag.attempt import Attempt
from dag.derived_node import DerivedNode

class StampDutyNode(DerivedNode[Money]):
    def __init__(self, node_id: str, purchase_price_node: UserInputNode[Money]):
        super().__init__(node_id, Money, deps=(purchase_price_node,))

    def compute(self, price_attempt: Attempt[Money]) -> Attempt[Money]:
        if not price_attempt.succeeded:
            return self._impossible({"price": price_attempt})
        price = price_attempt.value_or_none()
        if price < Money("250_000", "GBP"):
            return Attempt.succeeded(Money("0", "GBP"))
        # ... compute stamp duty ...
        return Attempt.succeeded(result)
```

Key points:

- **`compute(*dep_attempts)`** receives each dependency's `Attempt` in the same order as `deps`. Can be sync or async.
- **Auto-propagation:** Before calling `compute()`, the framework checks dependencies — if any dep is `impossible`, the node short-circuits to `Attempt.impossible()` without calling `compute()`. If any dep is `pending`, the node defers.
- **`_get_active_deps()`** returns the deps used for staleness checking and signal subscription. Override in `IfThenElseNode` for conditional dependency activation.
- **`_impossible(dep_attempts, extra)`** produces a detailed error message including each dep's state — use this instead of string formatting.
- **`schedule_retry(delay)`** schedules a DAG-level retry with exponential backoff. Returns `False` if max retries exceeded.
- **`provenance_source_type`** property — override to declare where data comes from (default `CALC`).
- **`provenance_formula`** property — override to return a `Formula` for display in the UI.

### `IfThenElseNode[T]` — Conditional branching

A special `DerivedNode` that conditionally activates either a *then* branch or an *else*
branch based on a synchronous predicate.

```python
from dag.if_then_else import IfThenElseNode

node = IfThenElseNode(
    node_id=node_id,
    value_type=Commute | None,
    condition_sources=(needs_rail_fare_node,),
    condition_fn=lambda needs: needs.succeeded and needs.value_or(False),
    then_branch=rail_fare_node,
    else_branch=driving_cost_node,
)
```

The predicate receives `Attempt` values for each condition source. The active
branch's output becomes this node's value. If neither branch is active, returns
`Attempt.succeeded(None)` (value type must be nullable).

---

## Lifecycle

### 1. Creation

```
UserInputNode constructed   ──► loads last persisted value from DB
DerivedNode constructed     ──► loads last persisted value from DB
                             ──► connects Signal/Slot to each dep's `changed`
                             ──► registers with scheduler
```

### 2. Push / Change

```
UserInputNode.push(value)   ──► persists to SQLite
                             ──► emits `changed` signal
                                  │
                                  ▼
                       DerivedNode._on_dep_changed()
                             ──► marks self stale
                             ──► scheduler.schedule(self)
```

### 3. Refresh (scheduler-driven)

```
Scheduler processes node:
  ──► checks _is_stale() (dep timestamps vs own computed_at)
  ──► if stale:
       ├─► gathers dep attempts
       ├─► if any dep is impossible  → short-circuit to Attempt.impossible
       ├─► if any dep is pending     → defer (return without computing)
       └─► calls compute(*dep_attempts)
            ├─► on Exception:
            │    ├─► transient (TimeoutError, HTTP 429/5xx) → schedule_retry
            │    └─► permanent → Attempt.impossible
            └─► on success:
                 ├─► persists result to SQLite
                 ├─► emits `changed` signal (cascading to downstream)
                 └─► calls scheduler.after_refresh(node)
```

### 4. Serialisation

```python
# to_json() — full output with provenance tree (expensive)
{
    "status": "succeeded",
    "value": 250000,
    "succeeded": true,
    "pending": false,
    "impossible": false,
    "provenance": {
        "label": "Stamp Duty",
        "sourceType": "calc",
        "sources": {
            "12345/purchase_price": {"label": "Purchase Price", ...}
        }
    }
}

# to_json_value() — lightweight, skips provenance tree
# Use for bulk-list endpoints
{
    "status": "succeeded",
    "value": 250000,
    "stale": false,
}
```

---

## Signals & Slots

The reactivity system uses `Signal`, `Connection`, and `Slot` from `dag/signals.py`.

```python
from dag.signals import Signal, Slot, Connection

signal = Signal()

# Connect a handler — returns a Connection
conn = signal.connect(my_handler)

# Disconnect
conn.disconnect()

# Slot wraps a bound method with a WeakMethod reference,
# enabling auto-cleanup when the handler object is garbage-collected.
slot = Slot(self._on_dep_changed)
signal.connect(slot)
```

- Signals are synchronous — handlers run inline on `emit()`.
- Handlers receive no arguments; they read current state from the emitting node.
- Dead slots are swept after every emit.

`DerivedNode` connects a `Slot` to each dependency's `changed` signal in `__init__`.
Call `.disconnect()` to tear down all connections (used during property removal).

---

## Scheduler

The scheduler (`dag/scheduler.py`) manages *when* stale `DerivedNode`s get refreshed.

### Production: `AsyncQueueScheduler`

A background `asyncio.PriorityQueue`. The default scheduler is shared across all
asyncio tasks. Start the background loop at application startup:

```python
from dag.scheduler import start_processor

processor_task = start_processor()  # returns asyncio.Task
```

The background loop dequeues events and calls `node.refresh()`. Events scheduled
in the future are deferred until their wall-clock time (supports retry-at).

### Test isolation

Override the scheduler per-context:

```python
from dag.scheduler import set_scheduler, reset_scheduler
from dag.scheduler import AsyncQueueScheduler

# In test setup
scheduler = AsyncQueueScheduler(respect_time=False)
set_scheduler(scheduler)

# After each test
reset_scheduler()
```

### Utility functions

- `flush_processor()` — synchronously process all currently scheduled nodes.
- `set_after_refresh(callback)` — hook called after every node refresh (used for logging/monitoring).

---

## Persistence

The persistence layer (`dag/persistence.py`) stores node results in a SQLite
`node_results` table with append-only rows.

### Schema

```sql
CREATE TABLE node_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL,
    result_json TEXT NOT NULL,
    dep_timestamps TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_nr_node ON node_results(node_id, created_at DESC);
```

### Key functions

- `init_db(db_path)` — initialise the database and create tables.
- `save_node_result(node_id, result_dict, dep_timestamps, created_at)` — append a new row.
- `latest_node_result(node_id)` — return the most recent row as a dict, or `None`.
- `property_created_at(rid)` — ISO timestamp of the earliest node_result for a property.
- `property_rids()` — return all distinct property RIDs with persisted nodes.
- `close_db()` — close and clear the cached connection.

### Test fixtures

In tests, replace the global DB connection with an in-memory SQLite:

```python
# From tests/unit/conftest.py
@pytest.fixture(autouse=True)
def _sqlite_memory():
    import dag.persistence as per
    saved = per._get_db
    per._get_db = lambda: _make_memory_db()  # creates tables
    yield
    per._get_db = saved
```

---

## HTTP Error Handling

`dag/http_error.py` provides `HttpError` — a structured exception carrying
HTTP status, headers, and Retry-After metadata. The DAG retry logic recognises
this type for transient error classification.

```python
from dag.http_error import HttpError, is_transient_http_error

raise HttpError(429, body="Too Many Requests")

err = HttpError(502, headers={"Retry-After": "30"})
err.retry_after      # 30.0
err.is_server_error()  # True
err.is_rate_limit()    # False
```

`DerivedNode._is_transient_error()` treats `HttpError` rate-limits and server
errors as retryable.

---

## Design Rules

1. **One concept, one node.** Every distinct derived value gets its own `DerivedNode`.
   If `compute()` would calculate something that isn't the node's named purpose,
   split it into a new node. See *New DAG Convention* in `dag-model.md`.

2. **No side effects in compute.** Derived nodes must not push values into other
   nodes. Use a pure dependency chain — the signal cascade handles propagation.

3. **Values must be typed classes, not dicts.** Use frozen dataclasses or Pydantic
   models so the value type is self-documenting and the TypeAdapter round-trips safely.

4. **Service returns wrapped in Attempt.** The DAG boundary always uses `Attempt` —
   never pass raw return values between nodes.

5. **Node versioning.** When `compute()` changes in a way that invalidates old
   persisted results, bump the node_id (e.g. `town_desc_v2`). Don't delete DB
   rows — the new ID starts `pending`, computes fresh, and old rows are orphaned.
