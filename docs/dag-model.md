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

All nodes are declared in `houses/model/nodes.py` using the `@node()`
decorator. The decorator registers the node in the global `NODES` dict.

```python
@node(id="my_value", kind=NodeKind.derived, deps=["dep_a", "dep_b"])
def my_value(dep_a: str | None, dep_b: str | None):
    ...
    return result, "my_provenance"
```

Node IDs must be unique. Dependencies are referenced by their node ID.

## Adding a New Node

1. **Declare the node** in `houses/model/nodes.py` with the `@node()` decorator.
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
