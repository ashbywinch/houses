# Phase 2 — Port the Base Module to DAG + SQLite

## Nodes

### Source nodes (set during enrichment, versioned — forced enrichment adds new rows)

| Node ID | Value | Provenance |
|---|---|---|
| `rid` | Rightmove ID string | derived from URL |
| `rightmove_url` | The Rightmove URL | user |
| `rightmove_address` | Address string as scraped from Rightmove | rightmove_scraper |
| `rightmove_bedrooms` | Bedroom count | rightmove_scraper |
| `rightmove_price` | Listing price | rightmove_scraper |
| `rightmove_lat` | Lat from Rightmove's embedded map | rightmove_map |
| `rightmove_lng` | Lng from Rightmove's embedded map | rightmove_map |
| `geocode_lat` | Lat from geocoding the address at enrichment time | geocoding:{service} |
| `geocode_lng` | Lng from geocoding the address at enrichment time | geocoding:{service} |

### User input nodes (append-only per-node tables)

| Node ID | What the user sets | Table |
|---|---|---|
| `corrected_address` | A better/fuller address string | `user_corrected_address` |
| `precise_lat` | Precise latitude from map picker | `user_precise_lat` |
| `precise_lng` | Precise longitude from map picker | `user_precise_lng` |

### Derived nodes (computed, track row IDs of deps in dep_versions)

| Node ID | Depends on | Compute |
|---|---|---|
| `best_address` | `corrected_address`, `rightmove_address` | corrected if set, else rightmove |
| `best_lat` | `precise_lat`, `precise_lng`, `best_address` | precise if set, else geocode(best_address) |
| `best_lng` | `precise_lat`, `precise_lng`, `best_address` | precise if set, else geocode(best_address) |
| `map_url` | `best_lat`, `best_lng` | Google Maps URL |

Coordinate priority:
1. `precise_lat/lng` if set → use those (provenance: "manual")
2. Otherwise → geocode(`best_address`) fresh (provenance: whichever geocoding service resolved it)

---

## SQLite schema

### Source values — new row per forced enrichment, never updated in place

```sql
CREATE TABLE source_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    value TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_sv_prop_node ON source_values(property_id, node_id);
```

"Current" value: `SELECT id, value FROM source_values WHERE property_id=? AND node_id=? ORDER BY created_at DESC LIMIT 1`

### User inputs — one table per node, append-only, obsoleted_at for history

```sql
CREATE TABLE user_corrected_address (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id TEXT NOT NULL,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL,
    obsoleted_at TEXT
);
CREATE INDEX idx_uca_prop ON user_corrected_address(property_id);

CREATE TABLE user_precise_lat (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id TEXT NOT NULL,
    value REAL NOT NULL,
    created_at TEXT NOT NULL,
    obsoleted_at TEXT
);

CREATE TABLE user_precise_lng (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id TEXT NOT NULL,
    value REAL NOT NULL,
    created_at TEXT NOT NULL,
    obsoleted_at TEXT
);
```

"Current" value: `SELECT id, value FROM user_* WHERE property_id=? AND obsoleted_at IS NULL`

### Derived values — latest computed value, dep_versions tracks row IDs

```sql
CREATE TABLE derived_values (
    property_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    value TEXT NOT NULL,
    dep_versions TEXT NOT NULL,
    source TEXT NOT NULL,
    error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (property_id, node_id)
);
```

`dep_versions` is JSON: `{"corrected_address": 5, "rightmove_address": 3, "precise_lat": null}` — key is the dep node ID, value is the **row ID** (FK) in that dep's table, or null if the dep was absent at computation time. Staleness is determined by comparing `created_at` timestamps, not row IDs.

---

## Staleness detection

Each derived node's `dep_versions` stores the **row ID** (FK) of each dependency at the time of computation. Staleness is determined by comparing **timestamps** — is the version we used older than the latest version?

**Staleness check on resolve:**

```
for dep_node_id, stored_row_id in derived_node.dep_versions:
    if stored_row_id is None:
        # Dep was absent when this was computed
        latest = latest row for dep_node_id  (source_values or user_* table)
        if latest exists → STALE (dep now has a value)
    else:
        stored_time = created_at of source_values/user_* WHERE id = stored_row_id
        latest_time = MAX(created_at) for this dep_node_id and property_id
        if latest_time > stored_time → STALE (newer version exists)
```

Cases:
- `stored_time = 2026-06-13T10:00`, `latest_time = 2026-06-14T15:00` → stale
- `stored_time = null`, `latest_time = 2026-06-14T15:00` → stale (was absent, now present)
- `stored_time = 2026-06-14T15:00`, `latest_time = 2026-06-14T15:00` → fresh
- `stored_time = null`, `latest_time = null` → fresh (still absent)

Key: we store row IDs for referential integrity (FK), but compare `created_at` timestamps to decide freshness. This communicates intent: "this derived value was computed from data that is now outdated."

---

## Resolver

Async (best_lat/lng may geocode). Algorithm:

1. Load latest row for each source node for this property → `{node_id: (row_id, value)}`
2. Load current row for each user input node → `{node_id: (row_id, value)}`
3. Load all derived_values for the property → `{node_id: (value, dep_versions, ...)}`
4. Topological sort of requested nodes (Kahn's)
5. For each node in order:
   - Source node → use from step 1
   - User input node → use from step 2
   - Derived node:
     - If in step 3: check staleness per dep_versions vs steps 1-2
     - If stale or absent: call compute(resolved deps), store to SQLite, update step 3
     - If fresh: use from step 3
6. Return `dict[node_id, NodeResult]`

---

## Resolver context: `PropertyData`

To avoid passing around multiple dicts, the resolver uses a `PropertyData` container that bundles all three data sources for a property:

```python
@dataclass
class PropertyData:
    rid: str
    sources: dict[str, SourceRow]       # node_id → (id, value, source, created_at)
    user_inputs: dict[str, UserRow]     # node_id → (id, value, created_at)
    derived: dict[str, DerivedRow]      # node_id → (value, dep_versions, source, error, updated_at)

@dataclass
class SourceRow:
    row_id: int
    value: Any
    source: str
    created_at: datetime

@dataclass
class UserRow:
    row_id: int
    value: Any
    created_at: datetime

@dataclass
class DerivedRow:
    value: Any
    dep_versions: dict[str, int | None]  # row IDs (FKs) — staleness is checked via created_at comparison
    source: str
    error: str | None
    updated_at: datetime
```

The `PropertyData` is loaded by `PersistenceService.load_property(rid)` and passed to the resolver. The resolver mutates `data.derived` as it recomputes stale nodes, then `PersistenceService.save_derived(rid, node_id, DerivedRow)` persists changes.

The `resolve_property(rid, node_ids) -> dict[node_id, NodeResult]` function orchestrates loading, resolving, and saving.

---

## Dual-write at enrichment time

In `server.py`, after `run_enrichment()` completes:

```python
from houses.model.persistence import insert_source_value

# Capture geocode_lat/lng from the PropertyLocation BEFORE it may be
# overridden by Rightmove scrape data (refactor in enrichment_runner.py).
insert_source_value(rid, "rid", rid, "derived")
insert_source_value(rid, "rightmove_url", enriched.url, "user")
insert_source_value(rid, "rightmove_address", enriched.address, "rightmove_scraper")
insert_source_value(rid, "rightmove_bedrooms", enriched.bedrooms, "rightmove_scraper")
insert_source_value(rid, "rightmove_price", enriched.price, "rightmove_scraper")
insert_source_value(rid, "rightmove_lat", enriched.approx_latitude, "rightmove_map")
insert_source_value(rid, "rightmove_lng", enriched.approx_longitude, "rightmove_map")
insert_source_value(rid, "geocode_lat", geocoded_lat, f"geocoding:{geo_source}")
insert_source_value(rid, "geocode_lng", geocoded_lng, f"geocoding:{geo_source}")

# Also compute and store initial derived values
resolve_property(rid, ["best_address", "best_lat", "best_lng", "map_url"])
```

Existing properties in the sheet already have user corrections in columns A–G (Address, Postcode, Actual Latitude, Actual Longitude). On first SQLite write for an existing property (next time it's enriched or viewed), these are imported as initial user_input rows.

---

## Property detail page

`GET /properties/{rid}` → HTML template

Layout (mobile-first):

```
┌────────────────────────────────────────┐
│ ← Properties    48 Acacia Avenue       │  ← header with back link
│                                        │
│ £450,000 · 3 bed                       │  ← summary bar
│                                        │
│ ┌─ ▼ Address & Location ────────────┐ │
│ │ Address                            │ │
│ │ "163 Grand Drive, London,          │ │
│ │  SW20 9NB"                         │ │
│ │ 🏷️ Rightmove                 [Edit]│ │
│ │   ↻ (stale — updating...)          │ │  ← orange spinner when stale
│ │                                    │ │
│ │ Location                           │ │
│ │ 51.4157° N · 0.2267° W            │ │
│ │ 🏷️ Geocoded: Google Maps          │ │
│ │                                    │ │
│ │ 📍 [View on Google Maps]           │ │
│ │                                    │ │
│ │ 🗺️ [Set precise location on map]   │ │
│ └────────────────────────────────────┘ │
│                                        │
│ ┌─ ▶ Commute & Area ────────────────┐ │
│ │ (collapsed, reads from sheet)      │ │
│ └────────────────────────────────────┘ │
│ ...                                    │
```

### Stale spinner per value

Each derived value on the page shows a spinner if any of its dependencies have a newer `created_at` than what's recorded in `dep_versions`. The page includes JS polling that re-checks staleness every few seconds. When the timestamps match (values are fresh), the spinner disappears.

For Phase 2, the base module derived nodes recompute immediately within the enhance request (best_address, best_lat/lng, map_url all resolve synchronously), so the stale window is effectively zero for base nodes. The spinner infrastructure is built for future ported modules (commute, schools) where recompute requires async re-enrichment.

### Enhancement UX (HTMX)

All inline editing uses **HTMX** (loaded from CDN in `base.html`). Each editable value is wrapped in an `hx-target` div that gets swapped on response:

1. **Edit address**: Click "Edit" → HTMX swaps the value display for a `<form hx-post="/properties/{rid}/enhance" hx-swap="outerHTML">` with a text input pre-filled with the current value. Submit → response is the updated value section with new provenance badge.

2. **Set precise location**: Click "Set precise location" → HTMX loads the map overlay partial (`hx-get="/properties/{rid}/map-picker"`). Map picker is a Leaflet map (OSM tiles) with click-to-drop-pin. Confirm button POSTs `lat` and `lng` to the enhance endpoint. Response replaces the map picker with the updated location section.

3. **Re-enrichment trigger**: After either edit, a hidden `<div hx-trigger="load delay:1s" hx-get="/properties/{rid}/status">` starts polling the staleness state and shows a "↻ Updating..." spinner next to any value whose `dep_versions` timestamps are now stale. When the background re-enrichment completes and derived values recompute, the spinner disappears.

### Map picker

Leaflet (from npm, bundled in `houses/static/`) with OpenStreetMap tiles. Click anywhere to drop a draggable pin. Lat/lng displayed below map. Confirm button saves both coordinates.

---

## Re-enrichment on data change

`BackgroundTasks` in the enhance endpoint calls:

```python
async def re_enrich_property(rid: str):
    data = load_property_data(rid)
    # Use latest user inputs + source values to build updated Property
    prop = Property(
        url=data.sources["rightmove_url"].value,
        address=data.user_inputs.get("corrected_address", data.sources["rightmove_address"]).value,
        postcode=extract_postcode(actual_address),
        bedrooms=data.sources["rightmove_bedrooms"].value,
        price=data.sources["rightmove_price"].value,
        actual_latitude=data.user_inputs.get("precise_lat").value if "precise_lat" in data.user_inputs else None,
        actual_longitude=data.user_inputs.get("precise_lng").value if "precise_lng" in data.user_inputs else None,
    )
    enriched = await run_enrichment(...)
    await write_enriched_row(enriched)
    # Insert new source_value rows (not update — new versions)
    insert_source_value(rid, "rightmove_address", enriched.address, "rightmove_scraper")
    # ... etc for all source nodes
    # Recompute derived nodes with new versions
    resolve_property(rid, ["best_address", "best_lat", "best_lng", "map_url"])
```

This inserts NEW rows into source_values (not updates), so any derived node computed against the old source rows becomes stale and will recompute on next resolve.

---

## Files

### New

| File | Contents |
|---|---|
| `houses/model/__init__.py` | NodeDef, NodeResult, node kind types |
| `houses/model/registry.py` | node() builder, NODES dict |
| `houses/model/nodes.py` | Base module node declarations with node() calls |
| `houses/model/resolver.py` | PropertyData, topo sort, staleness check, resolve loop |
| `houses/model/persistence.py` | SQLite init, CRUD for source_values, user_* tables, derived_values |
| `houses/templates/property_detail.html` | Detail page with sections, provenance badges, edit forms |
| `houses/static/js/detail.js` | Map picker init (Leaflet), HTMX response handlers (init map after swap, close map overlay) |
| `houses/static/css/detail.css` | Detail styles: sections, badges, spinner, map overlay |

### Modified

| File | Change |
|---|---|
| `houses/web/router.py` | Add `GET /properties/{rid}`, `POST /properties/{rid}/enhance` |
| `houses/templates/_card.html` | Wrap card body in `<a href="/properties/{rid}">` |
| `houses/enrichment_runner.py` | Capture `geocode_lat/lng` before rightmove override |
| `houses/server.py` | Call `insert_source_value()` + `resolve_property()` after enrichment |
| `houses/templates/base.html` | Add HTMX CDN script tag, add `detail.js` |
| `pyproject.toml` | Add `leaflet` dependency |

---

## Tests (per test-first: write before implementation)

| Test | Asserts |
|---|---|
| Source values insert & latest query | Multiple rows for same (property, node) → latest by created_at |
| User input insert & current query | New row obsoletes old, current query returns un-obsoleted |
| Derived value staleness — source changed | stored `created_at` < latest `created_at` → stale |
| Derived value staleness — user input changed | stored `created_at` < latest `created_at` → stale |
| Derived value staleness — dep now present | stored null, latest has timestamp → stale |
| Derived value staleness — dep now absent | stored timestamp, latest null → stale |
| Derived value staleness — no change | stored timestamp = latest timestamp → fresh |
| Topological sort | Simple, diamond, disconnected graphs |
| Resolve source node | Returns value from latest source_values row |
| Resolve user input node | Returns value from current user row |
| Resolve derived node (fresh) | Returns cached value, no compute call |
| Resolve derived node (stale) | Calls compute, stores new derived_values row |
| best_address priority | corrected > rightmove |
| best_lat priority | precise > geocode |
| best_lat geocodes fresh on address change | Recomputes best_lat with new address |
| Map URL format | Correct Google Maps URL |
| Enhance endpoint saves user input | POST creates new user_* row, obsoletes old |
| Enhance triggers re-enrichment | BackgroundTasks calls run_enrichment |
| Dual-write inserts source_values | After enrichment, source_values table has rows |
| Detail page renders | 200 with node values and provenance badges |
| Detail page 404 | Unknown RID → 404 |
| Stale spinner on detail page | Node with stale dep_versions shows spinner |
