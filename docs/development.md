# Development Guide

> **Production Sheet Access** — Never write one-off scripts that read or write the production Google Sheet directly. They duplicate auth logic, bypass the enrichment pipeline, and can't be reused. Instead, use existing scripts or endpoints. If existing tools can't do what you need, extend them or make new well designed tools — don't bodge a one-off.

## Setup

```bash
make setup        # Create venv, install dependencies
```

This installs the project and dev dependencies (pytest, ruff, coverage) using `uv`.

## Configuration

Configuration uses `pydantic-settings` with a `HOUSES_` prefix.

**API keys live in the shell environment** (`.zshrc`, `.bashrc`, etc.), NOT in `.env` or code. The `.env` file is for non-secret configuration defaults only. Never read, log, echo, or store API keys in files.

All env vars can be placed in a `.env` file at the project root for non-sensitive config, but secrets must come from the environment.

### Required Env Vars

```bash
# Google Sheets service account JSON (single line)
HOUSES_GOOGLE_SHEETS_SERVICE_ACCOUNT={"type":"service_account",...}

# Google Sheet ID (from sheet URL)
HOUSES_SHEET_ID=...

# TfL API key (free tier, for transit commute times)
TFL_API_KEY=...

# OpenRouteService API key (for driving distance + geocoding)
HEIGIT_API_KEY=...
```

### Optional Env Vars

```bash
# Google Maps API key (for walkability amenities — Places API)
GOOGLE_MAPS_API_KEY=...

# OpenRouter API key (for LLM town descriptions)
OPENROUTER_API_KEY=...


```

### Configuration Constants

| Setting | Default | Description |
|---------|---------|-------------|
| `simon_postcode` | SW1V 2QQ | Simon's work anchor |
| `lorena_postcode` | EC3A 7LP | Lorena's work anchor |
| `bracknell_postcode` | RG12 8YA | Bracknell office |
| `petrol_mpg` | 45.0 | Car fuel efficiency |
| `petrol_price_per_litre` | 1.45 | £/L |
| `school_search_radius_km` | 5.0 | Radius for school search |
| `working_weeks_per_year` | 46 | For yearly commute calculation |
| `weekly_simon_trips` | 1 | Days Simon commutes to London |
| `weekly_lorena_trips` | 2 | Days Lorena commutes to London |
| `weekly_bracknell_trips` | 1 | Days Simon drives to Bracknell |

## Running

```bash
make run          # Start dev server on http://127.0.0.1:8080 with auto-reload
```

Or manually:

```bash
uv run uvicorn houses.server:app --host 127.0.0.1 --port 8080 --reload
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

**Test principles:**
- Unit tests mock external APIs using `httpx.MockTransport` or equivalent
- Tests should not require API keys to run (unless marked `integration`)
- Column count alignment is tested and enforced
- All monetary values tested for numeric format (no £ prefix)

## Linting and Formatting

```bash
make lint         # Ruff check
make format       # Auto-fix formatting issues
```

Configuration in `pyproject.toml`: line length 120, target Python 3.12.

## Adding a New Enrichment Module

1. Create the module in `houses/` (e.g., `houses/walkability.py`)
2. Define any new models in `houses/models.py` (make fields optional/None by default)
3. Add config fields to `houses/config.py` if new API keys or settings needed
4. Wire the enrichment call into `houses/server.py`'s `inject_property` endpoint
5. Add columns to `COLUMN_HEADERS` in `houses/sheets.py`
6. Update `_row_values()` to format the new fields
7. Update `scripts/setup_sheet.py` if the View tab needs new XLOOKUP formulas
8. Add tests for the new module
9. Update this document if the pattern differs

All new enrichment should follow the existing pattern:
- **Fail gracefully**: If the API is unavailable or returns an error, log a warning and return None/default
- **In-memory cache**: Deduplicate API calls by postcode or town name within a session
- **No new dependencies**: Justify any new Python dependencies in the PR

## Sheet Setup

After cloning, run the setup script to create the Properties Data and Properties View tabs:

```bash
uv run python scripts/setup_sheet.py
```

This is idempotent — safe to run multiple times. The Properties Data tab is cleared once on first run, then never cleared again.

## Env File Template

See `.env.example` for all configurable environment variables with comments.

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
uv run python scripts/enrichment-diff.py > /tmp/diff.tsv

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
