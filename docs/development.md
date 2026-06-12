# Development Guide

> **Production Sheet Access** — When modifying the production sheet, always use existing scripts or endpoints. If existing tools can't do what you need, extend them or make new well designed tools.

## Setup

```bash
make setup        # Create venv, install dependencies
```

This installs the project and dev dependencies (pytest, ruff, coverage) using `uv`.

## Configuration

Configuration uses `pydantic-settings` with a `HOUSES_` prefix.

**API keys live in the shell environment** (`.zshrc`, `.bashrc`, etc.), NOT in `.env` or code. The `.env` file is for non-secret configuration defaults only. Never read, log, echo, or store API keys in files.

All env vars can be placed in a `.env` file at the project root for non-sensitive config, but secrets must come from the environment.

## Running

```bash
make run          # Start dev server on http://127.0.0.1:8080 with auto-reload
```

## Testing

```bash
make test                    # Unit tests (fast, mocked, no external API calls)
make test-integration        # Integration tests (hits real APIs — requires keys)
make coverage                # Test with coverage report
```

### Test Structure

```
tests/
├── test_server.py           # HTTP endpoint tests (TestClient)
├── test_sheets.py           # Row formatting, column alignment
├── test_enricher.py         # Enrichment logic (mocked APIs)
├── test_models.py           # Pydantic model validation
└── conftest.py              # Shared fixtures
```

**Test markers:**
- `@pytest.mark.integration` — tests that hit real external APIs (excluded from `make test`)

## Linting and Formatting

```bash
make lint         # Ruff check
make format       # Auto-fix formatting issues
```

Configuration in `pyproject.toml`: line length 120, target Python 3.12.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/inject-property` | Submit a property for enrichment and sheet write |
| GET | `/health` | Health check |

### POST /inject-property

**Request:**
```json
{
  "url": "https://www.rightmove.co.uk/properties/123456789",
  "address": "123 High Street, Maidenhead, SL6 1AA",
  "postcode": "SL6 1AA",
  "bedrooms": 3,
  "price": 650000
}
```

**Query parameters:**
| Param | Type | Description |
|-------|------|-------------|
| `dry_run` | bool | Skip sheet write, return enriched data only |
| `fields` | list | Re-enrich specific columns on an existing property. Bypasses the duplicate-property guard. Comma-separated: `simon,lorena,petrol,schools,walk_time,amenities,town,epc,council_tax,geo` |

**Re-enriching a single column** — e.g. re-run only council tax on an existing row:
```bash
curl -X POST "http://127.0.0.1:8080/inject-property?fields=council_tax" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.rightmove.co.uk/properties/123456789",
       "address": "123 High Street, Maidenhead, SL6 1AA",
       "postcode": "SL6 1AA"}'
```
This skips the "property already exists" check and only writes the council tax columns to the existing row.

**Response (200 — sheet not configured):**
```json
{
  "status": "ok",
  "note": "Sheets not configured",
  "data": { "... enriched fields ..." }
}
```

**Response (201 — sheet written):**
```json
{
  "status": "ok",
  "row_url": "https://docs.google.com/spreadsheets/..."
}
```

### POST /reprocess

Re-run enrichment for existing properties by Rightmove ID. Reads existing row data from the sheet, runs the specified fields, and writes only those columns back in-place.

**Request:**

```json
{
  "ids": ["162493277", "88375569"]
}
```

Omit `ids` to reprocess every row in the sheet.

**Query parameters:**

| Param | Type | Description |
|-------|------|-------------|
| `fields` | list (required) | Comma-separated enrichment fields to re-run: `simon,lorena,petrol,schools,walk_time,amenities,town,epc,council_tax,geo` |

**Examples:**

```bash
# Re-run council tax for specific properties
curl -X POST "http://127.0.0.1:8080/reprocess?fields=council_tax" \
  -H "Content-Type: application/json" \
  -d '{"ids": ["162493277"]}'

# Re-run EPC for all properties
curl -X POST "http://127.0.0.1:8080/reprocess?fields=epc" \
  -H "Content-Type: application/json" \
  -d '{}'
```

**Response:**
```json
{
  "status": "ok",
  "processed": 1,
  "total_requested": 1,
  "results": {
    "162493277": "updated"
  }
}
```

### Backfill View (preferred for re-running enrichment)

The `/backfill-view` endpoint is the primary way to re-run enrichment for existing properties. It reads from the Properties View tab, matches rows to Properties Data, and writes results.

```bash
# Re-run Lorena/Simon commute (including bus fares) for specific properties
curl -X POST "http://127.0.0.1:8080/backfill-view?force=true&fields=lorena,simon&rids=89141142,88639800"

# Re-run all fields for all properties
curl -X POST "http://127.0.0.1:8080/backfill-view?force=true"
```

**Query parameters:**

| Param | Type | Description |
|-------|------|-------------|
| `force` | bool | Overwrite existing values (default: only fill empty cells) |
| `fields` | list | Restrict to specific enrichment groups: `simon,lorena,petrol,schools,walk_time,amenities,town,epc,council_tax,geo` |
| `rids` | str | Comma-separated Rightmove IDs to process (others skipped) |
| `dry_run` | bool | Report what would happen without doing it |
| `no_write` | bool | Run enrichment (caching API results) but skip sheet writes |

Output is streamed as newline-delimited JSON so progress is visible in real-time.

### Enrichment Diff Verification

After any refactoring that changes enrichment logic (commute calculation,
school lookup, formatting), verify that the new code produces the expected
output before writing to the sheet:

```bash
# Ensure the dev server is running
make run

# Run the diff: re-enriches every property (no-write) and compares
# against the current live sheet
curl -X POST http://localhost:8080/properties/compare > /tmp/diff.tsv

# Review differences
less /tmp/diff.tsv
```

The diff output is a TSV with columns ``RID``, ``Field``, ``Old (sheet)``,
``New (enriched)``.  Every difference must be understood:

- **API rate limit** — new value is empty, old had a value.  Wait for
  quota reset or check the API key.
- **Walk time change** — expected: haversine was replaced by Google
  Routes API (real path vs straight-line).
- **New property enriched** — old was empty, new has a value.  Correct.
- **Unexpected** — investigate: write a failing test, fix, re-run diff.

### Bus Fare Data Pipeline

Bus fare data comes from the **BODS** (Bus Open Data Service) NeTEx fare datasets. The extraction script downloads fare data for commuter-belt operators, parses the NeTEx XML, and produces `data/bus_fares.json`.

```bash
# Extract bus fares from cached BODS files (fast, uses existing cache)
uv run python scripts/extract_bus_fares.py --cached-only

# Full from-scratch download and extraction
uv run python scripts/extract_bus_fares.py
```

**Flags:**
- `--cached-only` — read cached XML files from `data/bods_cache/` instead of re-downloading
- `--force` — ignore operator checkpoints and re-process everything

**What it does:**
1. Downloads NaPTAN stop coordinates (cached to `data/bods_stops.csv`)
2. Queries BODS API for fare datasets by operator NOC
3. Filters datasets by sub-operator name (exact description match)
4. Downloads and parses NeTEx XML files (line fares, network passes, fare tables)
5. Extracts zone structures, stop→zone mappings, and zone pair prices
6. Accumulates network fares (day/return passes) and applies them across files
7. Writes per-operator checkpoints to `data/.bus_fares_checkpoints/`, merged into `data/bus_fares.json`

**To update the sheet with new bus fares:**
1. Re-run extraction: `uv run python scripts/extract_bus_fares.py --cached-only`
2. Restart the dev server (picks up new `bus_fares.json`)
3. Trigger backfill for affected properties, e.g.:
   `curl -X POST "http://127.0.0.1:8080/backfill-view?force=true&fields=lorena,simon"`
