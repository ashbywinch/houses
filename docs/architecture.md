# Architecture

**Property Listing Scraper & Enrichment Engine**

## System Overview

Rightmove listings → Firefox Page Assist (BYOK LLM) extracts structured data → FastAPI `POST /api/properties` (:8765) → enrichment nodes compute and persist every value in the DAG (SQLite, `data/houses.db`).

Enrichment sequence: transit commutes (TfL, Simon/Lorena) → petrol cost (ORS drive, Bracknell) → nearest boys-eligible schools (GIAS CSV + postcodes.io) → walkability (Google Maps Places + ORS walking, planned) → town description (OpenRouter LLM, planned) → council tax (VOA scraper + CivAccount).

Tech stack and entry points: see `pyproject.toml` (dependencies) and the repo tree (`houses/`). Module responsibilities: see the Key Files mapping in `AGENTS.md`.

## Architectural Pattern

Layered Architecture with a Domain Model core (Hexagonal / Ports & Adapters variant):

| Layer | What it is | Rules | Where |
|---|---|---|---|
| **PRESENTATION** | FastAPI route handlers + templates | Never import infrastructure; never implement business logic (priority, validation, staleness); reads resolved data from Application | `houses/web/`, `houses/templates/` |
| **APPLICATION** | Orchestration — WHEN to enrich/resolve/display | Calls enrichment (infra) for raw data → pushes source nodes (domain) → drains the scheduler. Never re-implements DAG rules | `houses/server.py`, `houses/web/` |
| **DOMAIN MODEL** | The DAG — nodes, priority, staleness, resolution. "What is true" about a property | No HTTP/API/I/O. External code reads via `houses/property_registry.py` / node JSON — never direct DB queries | `houses/nodes/`, `dag/`, persistence in `dag/persistence.py` |
| **INFRASTRUCTURE** | Everything talking to the outside world | Implements service protocols from `services.py` (ports → adapters); called by Application, never by Domain | `houses/services.py`, `houses/location.py`, etc. |

### Enrichment cycle

Application calls an enrichment module (infra) → module returns raw data → Application pushes it into a source node (domain) → the scheduler checks staleness, runs compute, saves derived attempts → Application reads resolved values via the registry to render UI/API responses.

**The DAG knows nothing about HTTP or enrichment modules.** It receives source-node values and produces derived values.

## Dependency Injection

Three DI patterns:

### Services container (`houses/services.py`)

`Services` dataclass bundles every enrichment service with real defaults. `_run_enrichment` accepts optional `services` — production `None` → `Services()`; tests pass fakes from `tests/helpers.py`.

Protocols in `houses/services.py` document every module boundary (`GeocodingService`, `EPCLookupService`, `CouncilTaxService`, …). Agents read this file to learn what each module depends on.

### ContextVar + middleware (`houses/context.py` + server middleware)

Per-request state, auto-creating the production default when unset: `_request_scrape_fn` (Rightmove scraper seam). Names and types are in `houses/context.py`.

### Local `_kwarg` injection

Leaf functions accept optional underscore-prefixed params (`_registry`, `_page_path`, `_page_template`) with `None` defaults falling back to the real implementation. Tests pass pre-built objects directly — no monkeypatch.
