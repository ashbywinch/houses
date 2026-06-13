# AGENTS.md — Houses

**Browser-to-Spreadsheet Ingestion & Enrichment Engine.**

## Quick Start

```bash
make setup && make run          # Install + start dev server
make test                       # Run unit tests
make test-integration           # Integration tests
```

## Decision Tree

- **Develop / test / run**: [docs/development.md](docs/development.md)
- **Architecture overview**: [docs/architecture.md](docs/architecture.md)
- **Add a column**: [docs/column-reference.md](docs/column-reference.md)
- **Add an enrichment module**: [docs/adding-a-new-enrichment-module.md](docs/adding-a-new-enrichment-module.md)
- **Write docs**: [docs/writing-documentation.md](docs/writing-documentation.md)
- **Use the API**: [docs/api.md](docs/api.md)
- **Troubleshoot batch endpoints**: [docs/troubleshooting-endpoints.md](docs/troubleshooting-endpoints.md)

## Key Files

| File | Purpose |
|------|---------|
| `houses/server.py` | FastAPI app, endpoints, `_run_enrichment()` orchestration |
| `houses/services.py` | Service protocols + `Services` DI container (real/fake) |
| `houses/context.py` | ContextVar per-request state (bus fares, geo state, sheets client) |
| `houses/config.py` | Env-var configuration |
| `houses/sheets.py` | gspread integration, column headers |
| `tests/helpers.py` | Reusable fakes: `FakeCommuteRouter`, `FakeEPC`, `make_services()` |
