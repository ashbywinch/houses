# AGENTS.md — Houses

**Browser-to-Spreadsheet Ingestion & Enrichment Engine**

## What This Project Does

Houses is a local FastAPI server that acts as a webhook broker for property listing data. The user browses Rightmove in Firefox, extracts property details via a Page Assist sidepanel (BYOK LLM), and sends structured JSON to this server. The server enriches the data with transit commute times, petrol costs, local school info, walkability, town descriptions, and council tax data, then writes everything to a Google Sheet.

## Decision Tree: What Do You Want to Do?

### 1. Understand the Architecture
**Read**: [docs/architecture.md](docs/architecture.md)
- System overview and data flow
- Sheet architecture (Properties View / Properties Data)
- Tech stack and key files

### 2. Add or Modify a Column
**Read**: [docs/column-reference.md](docs/column-reference.md)
- Complete column layout for both tabs
- Data types and sources
- XLOOKUP formulas in the View tab
- Update process

### 3. Add a New Enrichment Module
**Read**: [docs/enrichment-modules.md](docs/enrichment-modules.md)
- Existing module patterns to follow
- API details and graceful degradation
- How to wire into the server
- [docs/development.md](docs/development.md) — setup and testing

### 4. Develop, Test, or Run the Server
**Read**: [docs/development.md](docs/development.md)
- Setup, configuration, env vars
- Running the server
- Testing and linting
- API endpoint reference

### 5. Write Documentation
**Read**: [docs/writing-documentation.md](docs/writing-documentation.md)
- Context Efficiency Principle
- Single source of truth
- One topic per file

### 6. Follow Coding Standards
**Read**: [docs/coding-standards.md](docs/coding-standards.md)
- Naming principles
- Module structure and SRP
- Fail fast, no over-abstraction

## Key Files

| File | Purpose |
|------|---------|
| `houses/server.py` | FastAPI app, `/inject-property` endpoint, startup/shutdown |
| `houses/models.py` | Pydantic models for property payload and enriched data |
| `houses/config.py` | Configuration — postcodes, API keys, sheet IDs |
| `houses/enricher.py` | Transit commute, petrol cost, and school lookup logic |
| `houses/sheets.py` | gspread integration, canonical column headers |
| `houses/retry.py` | Async retry with exponential backoff and jitter |
| `scripts/setup_sheet.py` | Sheet tab creation and XLOOKUP formula templates |

## Development Commands

```bash
make setup    # Create venv, install deps
make run      # Start dev server on :8080 (auto-reloads on code changes via --reload)
make test     # Run unit tests
make lint     # Ruff check
make format   # Auto-fix formatting
```

## Agent Rules

1. **Never write to Properties View** — use XLOOKUP formulas for cross-reference
2. **Primary key** is the Rightmove URL in Column A of Properties Data
3. **School constraint**: all schools must accept boys, non-fee-paying
4. **If closest secondary is girls-only**, substitute nearest co-ed/boys alternative
5. **Transit** uses public transport baselines (not driving) for Simon/Lorena
6. **Bracknell commute** uses petrol cost calculation (45mpg, £1.45/L)
7. **All commute costs in Properties Data are daily (return trip)** — Simon daily = 2× single TfL fare, Lorena daily = 2× single TfL fare, Bracknell daily = round-trip petrol
8. **Never archive deprecated files** — delete them. Obsolete content is a liability.
