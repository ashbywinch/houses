# DAG Data Model

The DAG (directed acyclic graph) model manages property data as a set of
interconnected **nodes**, each with a specific **kind** that determines how
its value is resolved.

```
source ────► user_input ──► derived ──► display
```

## Node Kinds

| Kind | Stored in | Resolved by | Example |
|---|---|---|---|
| `source` | `source_values` table | Latest row by `created_at` (append-only) | `rightmove_address`, `rightmove_location` |
| `user_input` | Dedicated `user_*` table (soft-delete) | Latest non-obsoleted row | `corrected_address`, `precise_location` |
| `derived` | `derived_values` table (upsert) | Compute function, cached until deps change | `best_address`, `best_location` |

### Source nodes

Source values come from enrichment (Rightmove scrape, geocoding). Each
re-enrichment appends a new row. The resolver always picks the latest row
per `node_id` for a property.

```sql
INSERT INTO source_values (property_id, node_id, value, source, created_at)
VALUES (?, ?, ?, ?, ?);
```

### User input nodes

User corrections (address edit, map picker) are stored in dedicated tables
with soft-delete: old rows get an `obsoleted_at` timestamp, new rows are
inserted. The resolver picks the latest non-obsoleted row.

Each user input node has a `user_table` field in its `NodeDef`:

```python
node(
    id="precise_location",
    kind=NodeKind.user_input,
    user_table="user_precise_location",
)
```

Add a new `CREATE TABLE` to `init_db()` in `persistence.py` and an entry
in `USER_TABLE_NODES`.

### Derived nodes

Derived nodes are recomputed when their dependencies change. The compute
function receives the resolved values of each dependency by name:

```python
@node(id="best_address", kind=NodeKind.derived, deps=["corrected_address", "rightmove_address"])
def best_address(corrected_address: str | None, rightmove_address: str | None):
    if corrected_address:
        return corrected_address, "user"
    return rightmove_address, "rightmove_scraper"
```

The return value is a tuple of `(value, provenance)` where provenance is a
short string shown as a badge on the detail page.

The resolver checks staleness: if any dependency has a newer row than when
this derived node was last computed, it recomputes.

## Node Registry

All nodes are declared in `houses/nodes/` using classes that inherit from

```python
@node(id="my_value", kind=NodeKind.derived, deps=["dep_a", "dep_b"])
def my_value(dep_a: str | None, dep_b: str | None):
    ...
    return result, "my_provenance"
```

Node IDs must be unique. Dependencies are referenced by their node ID.

## Adding a New Node

1. **Declare the node** in `houses/nodes/` as a class inheriting from `DerivedNode` or `UserInputNode`.
   - The decorator argument `id` is the node's key throughout the system.
   - For `source` or `user_input` nodes, declare without a compute function:

     ```python
     node(id="my_source", kind=NodeKind.source, provenance_template="my_enricher")(lambda: None)
     ```

2. **For user_input nodes**: Add a table to `init_db()` in `persistence.py`
   and an entry in `USER_TABLE_NODES`.

3. **Insert values**: Call `insert_source_value()` or `insert_user_input()`
   from the enrichment runner or the sheet import.

4. **Display in the template**: Add the node to the context dict in the
   route handler and reference it in the template.

5. **Write tests**:
   - Persistence: `test_persistence.py` — CRUD for the new value type
   - Compute logic: `test_nodes.py` — unit-test the compute function
   - Resolution: `test_enrichment_flow.py` — integration test through the
     resolver with seeded source/user_input values

## Persistence in Tests

The `_sqlite_memory` fixture in each test file replaces the global DB
connection with an in-memory SQLite database. This keeps tests isolated
and fast.

```python
@pytest.fixture(autouse=True)
def _sqlite_memory():
    import houses.model.persistence as per
    saved = per.get_db
    conn = _make_memory_db()  # creates tables
    per.get_db = lambda: conn
    yield
    per.get_db = saved
```

The fixture must create all the tables that `init_db()` creates.

## GeoPoint Values

Coordinate pairs are stored as `GeoPoint` objects. The persistence layer
serializes them to JSON for storage and deserializes on load.

When declaring a derived node that returns a `GeoPoint`, add the node ID
to `GEOPOINT_NODES` in `persistence.py` so the serializer handles it.

```python
GEOPOINT_NODES = {"rightmove_location", "geocode_location", "precise_location", "best_location"}
```

---

## New DAG Convention (houses/nodes/)

The DAG was replaced in 2026. Nodes now live in `houses/nodes/` using the
`dag/` library (`DerivedNode`, `UserInputNode`, `Attempt`, `Provenance`).

### One concept, one node

Every distinct derived value gets its own `DerivedNode` subclass in
`houses/nodes/`. If a node's `compute()` would calculate something that
isn't part of that node's named purpose, split it into a new node.

```python
# ✅ Correct — each calculation is its own node
class PetrolNode(DerivedNode[PetrolCost]):
    deps: (commute_node,)
    async def compute(self, commute: Attempt[CommuteResult]) -> Attempt[PetrolCost]:
        ...

class CommuteNode(DerivedNode[CommuteResult]):
    ...  # only commute logic, not petrol

# ❌ Wrong — CommuteNode should not calculate petrol costs
class CommuteNode(DerivedNode[CommuteResult]):
    async def compute(self, ...) -> Attempt[CommuteResult]:
        commute = calculate_commute(...)
        petrol = calculate_petrol(...)  # unrelated logic shoved in
        ...
```

**Why:**
- Each node has a single responsibility — test it in isolation, compose freely
- The signal chain tracks real dependencies; shoving unrelated logic into one
  node hides the dependency graph and causes unnecessary recomputation
- A node's return type maps directly to what it computes; one node returning
  a tuple of unrelated values means no downstream node can depend on just one
  of them
- Adding a new node costs ~30 lines and doesn't touch existing compute logic

**Signal to create a new node:** You're adding code to an existing `compute()`
that reads data the node didn't already use, or returns a value conceptually
independent of the node's current return type.

### Every compute MUST return Attempt[T]

No raw values, no `None`, no implicit returns. Every code path must end with
either `Attempt.succeeded(value)` or `Attempt.impossible(reason)`.

```python
# ✅ Correct
async def compute(self, dep: Attempt[str]) -> Attempt[int]:
    if not dep.succeeded:
        return self._impossible({"dep": dep})
    return Attempt.succeeded(len(dep.value_or_none()))

return None                                                  # not an Attempt at all
return old_attempt_object_without_check                      # never assume succeeded
```


### No side effects in computed nodes

Derived nodes must not push values into SourceNodes. Use a pure dependency
chain instead:

```python
# ✅ SchoolPostcodeNode: depends on school node, extracts postcode
class SchoolPostcodeNode(DerivedNode[str]):
    deps: (school_node,)  # signal chain handles staleness

    async def compute(self, school_attempt: Attempt[dict]) -> Attempt[str]:
        if school_attempt.succeeded:
            return Attempt.succeeded(school_attempt.value_or_none()["postcode"])

# ❌ Side-effect approach (never do this):
# school node pushes postcode to George's poi_src inside compute()
```

The signal chain propagates automatically:
1. School node computes, emits `changed`
2. SchoolPostcodeNode becomes stale (dep changed)
3. SchoolPostcodeNode recomputes, emits `changed`
4. TransitNode becomes stale (dep changed)
5. TransitNode recomputes with the new postcode

No manual `_sync_` methods needed.

### Values must be typed classes, not dicts

Commute data uses `CommuteResult` (frozen dataclass with `duration: Quantity`,
`daily_cost: Money`, `mode`, `details`). Never return plain dicts as node values.

```python
# ✅ Correct
return Attempt.succeeded(
    CommuteResult(duration=Quantity(32, "minute"), daily_cost=Money("4.50", "GBP"), ...),
    ...
)

# ❌ Wrong — no type safety, field names can drift
return Attempt.succeeded(
    {"duration": 32, "daily_cost": 4.50, ...},  # what's the schema?
    ...
)
```

### Service Protocol returns must be wrapped in Attempt

When a service returns `School | None`, the node's compute wraps it in
`Attempt.succeeded(...)` or `Attempt.impossible(...)` with the appropriate
provenance. The DAG boundary always uses Attempt — never pass raw values
between nodes.

### Walk commute detection

When `get_commute` returns a `Commute` object whose legs are all walking,
`TransitNode` sets `mode="walk"` so the frontend can use walk-specific
pill colour thresholds (15/30 instead of 45/75).

### Settings sources live in Services, not at module level

``persons_source``, ``financial_source`` and ``commute_thresholds_source``
are **not** module-level variables. They live in the ``Services`` DI
container and are created eagerly by ``Services.__init__``.

```python
# ✅ Correct — access via the container
from houses.context import get_services
svc = get_services()
data = await svc.persons_source.attempt()

# ❌ Wrong — no module-level import
from houses.nodes.settings import persons_source  # no longer exists
```

**Updating settings** at runtime uses the PATCH endpoint, which pushes
new values into the UserInputNode. The signal chain propagates the change
to all downstream computed nodes automatically:

```bash
curl -X PATCH http://localhost:8080/api/settings/persons \
  -H 'Content-Type: application/json' \
  -d '[...]'
```

**Never delete the database** to force a recompute. Use the PATCH
endpoint instead.

### DB isolation for tests

The ``_sqlite_memory`` fixture in ``tests/unit/conftest.py`` replaces
the global DB connection with an in-memory SQLite before every test.
Because settings sources are not module-level, they read from the
in-memory DB by default — no stale cached data, no real DB access
during test collection.
