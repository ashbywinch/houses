# AGENTS.md — Houses

**Browser-to-Spreadsheet Ingestion & Enrichment Engine**

## What This Project Does

Houses is a local FastAPI server that acts as a webhook broker for property listing data. The user browses Rightmove in Firefox, extracts property details via a Page Assist sidepanel (BYOK LLM), and sends structured JSON to this server. The server enriches the data with transit commute times, petrol costs, and local school info, then writes everything to a Google Sheet.

## Key Files

| File | Purpose |
|------|---------|
| `houses/server.py` | FastAPI app, `/inject-property` endpoint, startup/shutdown |
| `houses/models.py` | Pydantic models for property payload and enriched data |
| `houses/config.py` | Configuration — postcodes, API keys, sheet IDs |
| `houses/enricher.py` | Transit commute, petrol cost, and school lookup logic |
| `houses/sheets.py` | gspread integration — write to AI_Data_Source (Bot) tab |
| `Agent Briefing.txt` | Full system spec and constraints |

## Development Commands

```bash
make setup    # Create venv, install deps
make run      # Start dev server on :8080
make test     # Run tests
make lint     # Ruff check
make format   # Auto-fix formatting
```

## Sheet Architecture

- **Tab 1: Properties (Human)** — User-facing dashboard, manual edits, live XLOOKUP formulas
- **Tab 2: AI_Data_Source (Bot)** — Flat data warehouse, server has exclusive write access

## Agent Rules

1. Never write to the Properties (Human) tab — use XLOOKUP formulas for cross-reference
2. Primary key is the Rightmove URL in Column A of AI_Data_Source
3. School constraint: all schools must accept boys, non-fee-paying
4. If closest secondary is girls-only, substitute nearest co-ed/boys alternative
5. Transit calculations use public transport baselines (not driving) for Simon/Lorena
6. Bracknell commute uses petrol cost calculation (45mpg, £1.45/L)
