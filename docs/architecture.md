# Architecture

**Browser-to-Spreadsheet Ingestion & Enrichment Engine**

## System Overview

```
┌──────────────┐     POST /inject-property     ┌──────────────┐     enrich + write    ┌─────────────────────┐
│  Firefox     │  ──────────────────────────►   │  FastAPI      │  ──────────────────►  │  Google Sheets       │
│  + Page      │     {url, address, ...}       │  Server       │                      │  ┌───────────────┐   │
│  Assist      │                                │  :8080        │                      │  │ Properties    │   │
│  (BYOK LLM)  │  ◄──────────────────────────   │              │                      │  │ Data (Bot)    │   │
└──────────────┘     {status, enriched_data}    │  + Enrichers  │                      │  └───────────────┘   │
                                                │              │                      │  ┌───────────────┐   │
                                                │  TfL API      │                      │  │ Properties    │   │
                                                │  ORS API      │                      │  │ View (Human)  │   │
                                                │  Google Maps  │                      │  └───────────────┘   │
                                                │  OpenRouter   │                      └─────────────────────┘
                                                └──────────────┘
```

## Data Flow

1. **User browses** a Rightmove listing in Firefox.
2. **Page Assist sidepanel** (BYOK LLM) extracts structured data from the page HTML: URL, address, postcode, bedrooms, price.
3. **HTTP POST** sends the payload to `http://127.0.0.1:8080/inject-property`.
4. **Server receives** the payload and runs enrichment in sequence:
   - Transit commute times (TfL API) for Simon and Lorena
   - Petrol cost (ORS driving distance) for Bracknell
   - Nearest boys-eligible schools (GIAS CSV + postcodes.io)
   - Walkability (Google Maps Places + ORS walking) — planned
   - Town description (OpenRouter LLM) — planned
   - Council tax lookup (Homedata + CivAccount) — deferred
5. **Server writes** the full enriched row to the **Properties Data** tab in Google Sheets.
6. **Properties View** tab automatically picks up the new data via live XLOOKUP formulas.

## Sheet Architecture

### Why Two Tabs?

Google Sheets is collaborative but fragile. Writing directly to the human-facing tab would:
- Overwrite custom formatting, cell colors, and conditional formatting
- Clobber manual comments and WhatsApp notes
- Create data collisions if someone edits a row simultaneously

### Split-Tab Design

| Tab | Name | Access | Purpose |
|-----|------|--------|---------|
| 1 | **Properties View** | Manual edits only | Human dashboard — naming, comments, status, live formulas |
| 2 | **Properties Data** | Server write-only | Flat data warehouse — all enrichment fields, one row per property |

The primary key linking both tabs is the **Rightmove URL** (Column A in Properties Data, Column B in Properties View).

**Critical rule**: The server never writes to Properties View. The View tab pulls data from the Data tab using `XLOOKUP` formulas. See [column-reference.md](column-reference.md) for the exact formula layout.

## Tech Stack

| Component | Technology | Notes |
|-----------|------------|-------|
| Server framework | FastAPI + uvicorn | Async, auto-docs at /docs |
| Data models | Pydantic v2 | Validation, serialization |
| Configuration | pydantic-settings | Env vars with HOUSES_ prefix |
| HTTP client | httpx | Async, used for all external APIs |
| Sheet integration | gspread + google-auth | Service account authentication |
| Transit API | TfL Unified API (free) | Journey planning, fare extraction |
| Driving distance | OpenRouteService (ORS) | Driving-car profile, geocoding |
| Schools data | GIAS CSV (gov.uk) | All establishments, enriched with Ofsted |
| Geocoding | postcodes.io (free) + ORS Pelias | Full postcodes + fallback for outcodes |
| Walkability | Google Maps Places API (New) | Nearby Search for amenities |
| Town descriptions | OpenRouter (BYOK LLM) | LLM-generated descriptions |
| Council tax | Homedata + CivAccount | Deferred — awaiting API key |

## Key Files

| File | Responsibility |
|------|----------------|
| `houses/server.py` | FastAPI app, `/inject-property` endpoint, startup/shutdown |
| `houses/models.py` | Pydantic models for property payload and enriched data |
| `houses/config.py` | Configuration — postcodes, API keys, constants |
| `houses/enricher.py` | Transit commute, petrol cost, school lookup |
| `houses/sheets.py` | gspread integration, column headers (canonical), row formatting |
| `houses/retry.py` | Async retry with exponential backoff and jitter |
| `houses/walkability.py` | Google Maps Places + ORS walking (planned) |
| `houses/town_desc.py` | LLM-generated town descriptions (planned) |
| `houses/council_tax.py` | Council tax lookup stub (deferred) |
| `scripts/setup_sheet.py` | Sheet tab creation, XLOOKUP formula templates |
| `scripts/enrich_with_ofsted.py` | Ofsted data merge into school CSV |
| `Agent Briefing.txt` | **Archived** — see `docs/` for current documentation |
