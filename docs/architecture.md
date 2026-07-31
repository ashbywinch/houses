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

1. User browses a Rightmove listing in Firefox.
2. Page Assist sidepanel (BYOK LLM) extracts structured data: URL, address, postcode, bedrooms, price.
3. HTTP POST to `http://127.0.0.1:8080/inject-property`.
4. Server runs enrichment in sequence: transit commutes (TfL, Simon/Lorena) → petrol cost (ORS drive, Bracknell) → nearest boys-eligible schools (GIAS CSV + postcodes.io) → walkability (Google Maps Places + ORS walking, planned) → town description (OpenRouter LLM, planned) → council tax (VOA scraper + CivAccount).
5. Server writes the full enriched row to **Properties Data** tab.
6. **Properties View** tab picks it up via live XLOOKUP formulas.

## Sheet Architecture

### Why two tabs?

Google Sheets is collaborative but fragile. Writing directly to the human tab would: overwrite formatting/colors/conditional formatting; clobber manual comments and WhatsApp notes; collide on simultaneous edits.

### Split-tab design

| Tab | Name | Access | Purpose |
|-----|------|--------|---------|
| 1 | **Properties View** | Manual edits only | Human dashboard — naming, comments, status, live formulas |
| 2 | **Properties Data** | Server write-only | Flat warehouse — all enrichment fields, one row per property |

Primary key linking tabs: **Rightmove URL** (col A in Data, col B in View).

**Critical rule:** the server never writes to Properties View. The View tab pulls from Data via `XLOOKUP` formulas — see [column-reference.md](column-reference.md).

## Tech Stack

| Component | Technology | Notes |
|-----------|------------|-------|
| Server framework | FastAPI + uvicorn | Async, auto-docs at /docs |
| Data models | Pydantic v2 | Validation, serialization |
| Configuration | pydantic-settings | Env vars, HOUSES_ prefix |
| HTTP client | httpx | Async, all external APIs |
| Sheet integration | gspread + google-auth | Service account auth |
| Transit API | TfL Unified API (free) | Journey planning, fare extraction |
| Driving distance | OpenRouteService (ORS) | Driving-car profile, geocoding |
| Schools data | GIAS CSV (gov.uk) | Enriched with Ofsted |
| Geocoding | postcodes.io (free) + ORS Pelias | Full postcodes + outcode fallback |
| Walkability | Google Maps Places API (New) | Nearby Search for amenities |
| Town descriptions | OpenRouter (BYOK LLM) | LLM-generated |
| Council tax | VOA scraper + CivAccount | Live — scrapes public gov.uk page |

## Architectural Pattern

Layered Architecture with a Domain Model core (Hexagonal / Ports & Adapters variant):

| Layer | What it is | Rules | Files |
|---|---|---|---|
| **PRESENTATION** | FastAPI route handlers + Jinja2 templates | Never import infrastructure; never implement business logic (priority, validation, staleness); reads resolved data from Application | `server.py`, `web/router.py`, `web/card_data.py`, `templates/*`, `static/*` |
| **APPLICATION** | Orchestration — WHEN to enrich/resolve/display | Calls enrichment (infra) for raw data → `insert_source_value` (domain) → `resolve_property` (domain) → reads results. Never re-implements DAG rules | `enrichment_runner.py`, `server.py`, `web/card_data.py`, `web/router.py` (import logic) |
| **DOMAIN MODEL** | The DAG — nodes, priority chains, staleness, resolution. "What is true" about a property | No HTTP/API/sheets/I/O. Persistence boundary at `model/persistence.py`. External code reads via `resolve_property`/`load_property_data` — never direct DB queries | `model/__init__.py`, `model/registry.py`, `houses/nodes/`, `model/resolver.py`, `model/persistence.py` |
| **INFRASTRUCTURE** | Everything talking to the outside world | Implements service protocols from `services.py` (ports → adapters); called by Application, never by Domain | `services.py`, `sheets/`, `location.py`, `transit_route.py`, `endpoint_client.py`, `council_tax.py`, etc. |

### Data flow

```
Browser request  →  Route handler (Presentation)
  →  get_all_cards()  (Application)
    →  get_data_rows()  (Infrastructure: Sheet read)
    →  insert_source_value()  (Domain: persistence)
    →  resolve_property()  (Domain: resolver)
    →  read derived_value  (Domain: persistence)
  →  render template  (Presentation)
```

Every enrichment cycle: Application calls enrichment module (infra) → module returns raw data → Application stores as source_value (domain) → calls `resolve_property()` → resolver checks staleness, runs compute, saves derived → Application reads resolved values → writes sheet or renders template.

**The DAG knows nothing about steps 1, 2, or 7.** It receives `source_values`, produces `derived_values`.

### Proposed improvements

1. **`sync_property()`** — one Application function replacing ad-hoc enrich→write→resolve→display scatter:
   ```python
   async def sync_property(rid: str, trigger_enrichment: bool = True) -> PropertyData:
       if trigger_enrichment:
           enriched = await _run_enrichment(rid, ...)  # writes source_values internally
       return await resolve_property(rid)
   ```
   `get_all_cards()` calls it per RID; the card assembler reads `PropertyData` (never sheet/DB directly). Eliminates the `_seed_dag_from_row` + `_enrich_from_dag` + inline `resolve_property` scatter.
2. **All enrichment output through the DAG** — every module writes `source_values` first; one sheet-write step reads `derived_values`. Gives full version history per recomputation.
3. **Extract sheet import** — `_try_import_from_sheet()` lives in `router.py` (presentation+infra mix). Move to e.g. `sheets/importer.py`, called by Application; route handler delegates.
4. **Split `card_data.py`** — `sheets/reader.py` (raw reading, exists) + `web/view_models.py` (pure ViewModel assembly, takes `PropertyData` + sheet metadata → `CardData`); DAG sync stays in Application (`get_all_cards` or new `houses/sync.py`).
5. **Register all enrichment fields as DAG nodes** — today only address/location are nodes. Every future field (commute, schools, EPC, council tax) gets source + derived node declarations → consistent staleness, recomputation, priority.

## Key Files

| File | Responsibility |
|------|----------------|
| `houses/server.py` | FastAPI app, `/inject-property`, startup/shutdown |
| `houses/enricher.py` | Commute computation, petrol cost, commute breakdown |
| `houses/routing.py` | Commute decision logic — walking, TfL transit, driving |
| `houses/transit_route.py` | TfL API wrapper, park-and-ride, parking costs |
| `houses/location.py` | Geocoding — postcodes.io, Google Maps, ORS, Nominatim |
| `houses/sheets/` | gspread, column headers (`Row`), View tab sync (`View`), named ranges, formulas |
| `houses/endpoint_client.py` | Reusable API client: Retry-After + budget tracking |
| `houses/services.py` | Service protocols + `Services` DI container |
| `houses/context.py` | ContextVar per-request state (bus fares, geo state, sheets) |
| `houses/config.py` | Configuration — postcodes, API keys, constants |
| `tests/helpers.py` | Fakes: `FakeCommuteRouter`, `FakeEPC`, `make_services()` |
| `houses/model/`, `houses/nodes/` | DAG registry (`registry.py`), node declarations, resolver, persistence |
| `houses/retry.py` | Async retry, exponential backoff + jitter |
| `houses/walkability.py` | Google Maps Places + ORS walking (planned) |
| `houses/town_desc.py` | LLM town descriptions (planned) |
| `houses/council_tax.py` | Council tax lookup (VOA scraper + CivAccount) |
| `scripts/setup_sheet.py` | Sheet tab creation, XLOOKUP formula templates |
| `scripts/enrich_with_ofsted.py` | Ofsted merge into school CSV |
| `Agent Briefing.txt` | **Archived** — see `docs/` |

## Dependency Injection

Three DI patterns:

### Services container (`houses/services.py`)

`Services` dataclass bundles every enrichment service with real defaults. `_run_enrichment` accepts optional `services` — production `None` → `Services()`; tests pass fakes from `tests/helpers.py`.

Protocols in `houses/services.py` document every module boundary (`GeocodingService`, `CommuteRoutingService`, `EPCLookupService`, `CouncilTaxService`, …). Agents read this file to learn what each module depends on.

### ContextVar + middleware (`houses/context.py` + `server.py` middleware)

Per-request state, auto-creating production defaults when unset:

| Variable | Purpose |
|----------|---------|
| `_request_services` | Active `Services` instance for the request |
| `_request_bus_fares` | `BusJourneyRegistry` (shared across routing + transit_route) |
| `_request_sheets_client` | Mock sheets client (set by test fixtures) |

`_geo_state` (rate-limit tracking) and `_geo_cache_var` (in-memory geocode cache) are also per-request, initialized by the same middleware.

### Local `_kwarg` injection

Leaf functions accept optional underscore-prefixed params (`_registry`, `_page_path`, `_page_template`) with `None` defaults falling back to the real implementation. Tests pass pre-built objects directly — no monkeypatch.
