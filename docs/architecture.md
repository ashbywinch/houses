# Architecture

**Browser-to-Spreadsheet Ingestion & Enrichment Engine**

## System Overview

Rightmove listings → Firefox Page Assist (BYOK LLM) extracts structured data → FastAPI `POST /inject-property` (:8765) → enrichment in sequence → Google Sheets (Properties Data tab written by server; Properties View tab pulls via XLOOKUP formulas).

Enrichment sequence: transit commutes (TfL, Simon/Lorena) → petrol cost (ORS drive, Bracknell) → nearest boys-eligible schools (GIAS CSV + postcodes.io) → walkability (Google Maps Places + ORS walking, planned) → town description (OpenRouter LLM, planned) → council tax (VOA scraper + CivAccount).

Tech stack and entry points: see `pyproject.toml` (dependencies) and the repo tree (`houses/`). Module responsibilities: see the Key Files mapping in `AGENTS.md`.

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

## Architectural Pattern

Layered Architecture with a Domain Model core (Hexagonal / Ports & Adapters variant):

| Layer | What it is | Rules | Where |
|---|---|---|---|
| **PRESENTATION** | FastAPI route handlers + templates | Never import infrastructure; never implement business logic (priority, validation, staleness); reads resolved data from Application | `houses/web/`, `houses/templates/` |
| **APPLICATION** | Orchestration — WHEN to enrich/resolve/display | Calls enrichment (infra) for raw data → `insert_source_value` (domain) → `resolve_property` (domain) → reads results. Never re-implements DAG rules | `houses/server.py`, `houses/web/card_data.py`, `houses/enrichment_runner.py` |
| **DOMAIN MODEL** | The DAG — nodes, priority, staleness, resolution. "What is true" about a property | No HTTP/API/sheets/I/O. External code reads via `resolve_property`/`load_property_data` — never direct DB queries | `houses/nodes/`, `dag/`, persistence in `dag/persistence.py` |
| **INFRASTRUCTURE** | Everything talking to the outside world | Implements service protocols from `services.py` (ports → adapters); called by Application, never by Domain | `houses/services.py`, `houses/sheets/`, `houses/location.py`, etc. |

### Enrichment cycle

Application calls enrichment module (infra) → module returns raw data → Application stores as source_value (domain) → calls `resolve_property()` → resolver checks staleness, runs compute, saves derived → Application reads resolved values → writes sheet or renders template.

**The DAG knows nothing about HTTP, the sheet, or enrichment modules.** It receives `source_values`, produces `derived_values`.

### Proposed improvements

1. **`sync_property()`** — one Application function replacing ad-hoc enrich→write→resolve→display scatter; `get_all_cards()` calls it per RID, card assembler reads the returned `PropertyData` (never sheet/DB directly). Eliminates the `_seed_dag_from_row` + `_enrich_from_dag` + inline `resolve_property` scatter.
2. **All enrichment output through the DAG** — every module writes `source_values` first; one sheet-write step reads `derived_values`. Gives full version history per recomputation.
3. **Extract sheet import** — `_try_import_from_sheet()` lives in `houses/web/api_router.py` (presentation+infra mix). Move to e.g. `houses/sheets/importer.py`, called by Application; route handler delegates.
4. **Split `card_data.py`** — `sheets/reader.py` (raw reading, exists) + `web/view_models.py` (pure ViewModel assembly); DAG sync stays in Application.
5. **Register all enrichment fields as DAG nodes** — today only address/location are nodes. Every future field gets source + derived node declarations → consistent staleness, recomputation, priority.

## Dependency Injection

Three DI patterns:

### Services container (`houses/services.py`)

`Services` dataclass bundles every enrichment service with real defaults. `_run_enrichment` accepts optional `services` — production `None` → `Services()`; tests pass fakes from `tests/helpers.py`.

Protocols in `houses/services.py` document every module boundary (`GeocodingService`, `EPCLookupService`, `CouncilTaxService`, …). Agents read this file to learn what each module depends on.

### ContextVar + middleware (`houses/context.py` + server middleware)

Per-request state, auto-creating production defaults when unset: `_request_services`, `_request_bus_fares`, `_request_sheets_client`, plus `_geo_state` (rate-limit tracking) and `_geo_cache_var` (geocode cache). Names and types are in `houses/context.py`.

### Local `_kwarg` injection

Leaf functions accept optional underscore-prefixed params (`_registry`, `_page_path`, `_page_template`) with `None` defaults falling back to the real implementation. Tests pass pre-built objects directly — no monkeypatch.
