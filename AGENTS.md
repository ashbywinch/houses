# AGENTS.md — Houses

**Browser-to-Spreadsheet Ingestion & Enrichment Engine**

## What This Project Does

Houses is a tool allowing users to compare a large number of properties for purchase. It enriches Rightmove property data with information about local schools, commutes, the local area, and the cost of purchase and ownership.

The system consists of a local FastAPI server acting as a front end for a Google spreadsheet. The user identifies properties on Rightmove and uses API endpoints to add them to the system.

## Decision Tree: What Do You Want to Do?

### 1. Develop, Test, or Run the Server
**Read**: [docs/development.md](docs/development.md)
**Read**: [docs/coding-standards.md](docs/coding-standards.md)

### 2. Understand the Architecture
**Read**: [docs/architecture.md](docs/architecture.md)
- System overview and data flow
- Sheet architecture (Properties View / Properties Data)
- Tech stack and key files

### 3. Add or Modify a Column
**Read**: [docs/column-reference.md](docs/column-reference.md)
- Complete column layout for both tabs
- Data types and sources
- XLOOKUP formulas in the View tab
- Update process

### 4. Add a New Enrichment Module
**Read**: [docs/enrichment-modules.md](docs/enrichment-modules.md)
- Existing module patterns to follow
- API details and graceful degradation
- How to wire into the server
- [docs/development.md](docs/development.md) — setup and testing

### 5. Write Documentation
**Read**: [docs/writing-documentation.md](docs/writing-documentation.md)

### 6. Troubleshoot Batch Endpoints
**Read**: [docs/troubleshooting-endpoints.md](docs/troubleshooting-endpoints.md)
- Before running batch operations
- What to check when results don't appear
- How to verify a batch actually completed
- Server reload pitfalls

## Key Files

| File | Purpose |
|------|---------|
| `houses/server.py` | FastAPI app |
| `houses/config.py` | Configuration — postcodes, API keys, sheet IDs |
| `houses/enricher.py` | Enrichment coordinators (orchestrates routing → schools → etc) |
| `houses/endpoint_client.py` | Reusable API client with Retry-After support |
| `houses/retry.py` | Async retry with exponential backoff and jitter |
| `houses/sheets.py` | gspread integration, canonical column headers |
| `houses/retry.py` | Async retry with exponential backoff and jitter |
| `POST /properties` | Upsert a property (enrich + write to sheet) |
| `GET /properties` | List all properties with enrichment data |
| `GET /properties/{rid}` | Get a single property by Rightmove ID |
| `POST /properties/compare` | Compare sheet vs fresh enrichment (TSV diff) |
| `POST /sheet/setup` | Setup sheet structure (tabs, headers, formulas) |

