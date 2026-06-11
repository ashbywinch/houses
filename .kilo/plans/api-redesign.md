# API Redesign: RESTful Endpoints

## Principles

Following standard REST conventions:
- **Nouns for resources, not verbs** — HTTP methods express the action
- **Plural nouns for collections**
- **Lowercase with hyphens**
- **Query parameters for filtering**

The underlying storage (Google Sheets) is an implementation detail.  The API
exposes **properties** — users don't know or care about tabs.

## Proposed Endpoints

### `GET /properties` — List all properties

Returns all properties with their enrichment data.  The server merges the
Data and View tabs transparently.

**Response:** `{ "properties": [{ "url": "...", "address": "...", ... }] }`

---

### `GET /properties/{rid}` — Get a single property

**Response:** a single enriched property object

---

### `POST /properties` — Upsert a property

Enrich a single property (request body), or re-enrich properties already in
the sheet (query params).  Always runs enrichment (POST means "create/update
the resource").  Keyed on Rightmove ID extracted from the URL.

Single property:
```json
POST /properties
Body: { "url": "...", "address": "...", "postcode": "...", ... }
```

Re-enrich existing properties:
```
POST /properties
Query: no_write=true&rids=88275093,173431283
```

Parameters:
- `no_write` (bool) — run enrichment (cache all API results) but don't
  write to the sheet.  Default `false`.
- `rids` (list) — restrict to specific RIDs.  Omit to process all.
- `fields` (list) — restrict to specific enrichment groups.

Replaces: `POST /inject-property`, `POST /backfill-view`
Replaces: `scripts/dump_sheet.py`

---

### `POST /properties/compare` — Compare enrichment with current data

Convenience wrapper around `POST /properties?no_write=true` that also reads
the current property data and diffs every column.  POST because it triggers
enrichment (side effect: API calls, caching).

Parameters:
- `rids` — restrict to specific RIDs (default: all)
- `fields` — restrict to specific enrichment groups (default: all)

Replaces: `scripts/enrichment-diff.py`

---

### `POST /sheet/setup` — Setup sheet structure

Creates tabs, column headers, XLOOKUP formulas.  The only endpoint that
leaks the sheet implementation — unavoidable since this action creates
the sheet itself.

Replaces: `scripts/setup_sheet.py`

## Migration

1. Add new endpoints alongside the old ones.
2. **Delete the old scripts** — no wrappers, no backwards compatibility.
3. Remove old endpoints after verifying everything works.
