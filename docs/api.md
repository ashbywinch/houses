# API Reference

All endpoints live on port 8080. The dev server: `make run`.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/properties` | List all properties |
| GET | `/properties/{rid}` | Get one property |
| POST | `/properties` | Batch refresh or submit one property |
| POST | `/properties/compare` | Compare sheet vs fresh enrichment (TSV diff) |
| POST | `/sync-view-formulas` | Refresh View tab XLOOKUP formulas after column changes |

---

## GET /health

Returns `{"status": "ok"}` when the server is running.

---

## GET /properties

List all properties from the sheet.

**Query parameters:**

| Param | Required | Description |
|-------|----------|-------------|
| `tab` | yes | `"view"` or `"data"` |

**Example:**
```bash
curl "http://localhost:8080/properties?tab=data"
```

---

## GET /properties/{rid}

Get a single property by Rightmove ID.

Detects duplicate RIDs and returns `409 Conflict` with a clear message.

**Query parameters:**

| Param | Required | Description |
|-------|----------|-------------|
| `tab` | yes | `"view"` or `"data"` |

**Example:**
```bash
curl "http://localhost:8080/properties/162456239?tab=data"
```

---

## POST /properties

Upsert a property or run a batch refresh.

### Single Property (no `rids`)

Submit a property for enrichment and sheet write:

```bash
curl -X POST http://localhost:8080/properties \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.rightmove.co.uk/properties/123456789",
       "address": "123 High Street, Maidenhead, SL6 1AA",
       "postcode": "SL6 1AA"}'
```

**Optional JSON fields:** `bedrooms`, `price`.

### Batch Refresh (with `rids`)

Reads from the View tab, matches to Data tab, enriches and writes back.

Output is streamed as newline-delimited JSON:

```json
{"type": "start", "total": 42, "force": false}
{"type": "row", "row": 2, "rid": "88275093", "status": "updated", "fields": ["lorena", "simon"]}
{"type": "row", "row": 3, "rid": "173431283", "status": "skipped", "reason": "already fully enriched"}
{"type": "summary", "updated": 40, "skipped": 1, "created": 0, "errors": 0}
```

**Query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `fields` | list | all | Comma-separated enrichment groups: `simon,lorena,petrol,schools,walk_time,amenities,town,epc,council_tax,geo` |
| `rids` | str | all | Comma-separated Rightmove IDs to process |
| `force` | bool | false | `true` = overwrite existing cells; `false` = only fill blanks |
| `no_write` | bool | false | Run enrichment without writing to the sheet |

**Examples:**

```bash
# Force refresh Simon/Lorena commute for all rows
curl -X POST "http://localhost:8080/properties?fields=simon,lorena&force=true"

# Fill blank cells for schools + walk_time
curl -X POST "http://localhost:8080/properties?fields=schools&fields=walk_time"

# Force refresh specific properties only
curl -X POST "http://localhost:8080/properties?fields=simon,lorena&force=true&rids=88275093,173431283"

# Dry run — see enriched data without writing
curl -X POST "http://localhost:8080/properties?no_write=true"
```

---

## POST /properties/compare

Re-enrich every property (no-write) and output a TSV diff against the
current sheet. Used to verify that refactoring hasn't changed output.

```bash
curl -X POST http://localhost:8080/properties/compare > /tmp/diff.tsv
```

The diff has columns ``RID``, ``Field``, ``Old (sheet)``, ``New (enriched)``.

**Query parameters:**

| Param | Type | Description |
|-------|------|-------------|
| `rids` | str | Comma-separated RIDs to compare (default: all) |
| `fields` | list | Comma-separated enrichment groups to compare |

---

## POST /sync-view-formulas

Refresh View tab XLOOKUP formulas and named ranges after a column
migration. Safe to call multiple times.

```bash
curl -X POST http://localhost:8080/sync-view-formulas
```
