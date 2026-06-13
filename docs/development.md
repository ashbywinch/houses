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
├── helpers.py               # Reusable fakes + make_services() factory
├── conftest.py
├── unit/                    # Pure function tests, _kwarg injection
│   ├── test_routing.py
│   ├── test_enricher.py
│   └── ...
├── integration/              # Full pipeline with fakes or MockTransport
│   ├── test_server.py        # HTTP endpoint tests (TestClient)
│   └── conftest.py           # MockTransport, cache isolation
└── e2e/                      # Real API calls (skipped by default)
```

### DI Patterns for Tests

See `docs/coding-standards.md` → *Dependency Injection* for the three
patterns and when to use each:

| Pattern | When |
|---------|------|
| `Services` container | Replace an entire enrichment module |
| `ContextVar` | Set per-request state (bus fares, sheets client) |
| `_kwarg` | Pass a specific data object to a leaf function |

**Test markers:**
- No marker — unit tests (fast, no external calls)
- `@pytest.mark.integration` — full pipeline tests (excluded from `make test`)

## Linting and Formatting

```bash
make lint         # Ruff check
make format       # Auto-fix formatting issues
```

Configuration in `pyproject.toml`: line length 120, target Python 3.12.

## Sheet Setup

After cloning, run the setup script to create the Properties Data and Properties View tabs:

```bash
uv run python scripts/setup_sheet.py
```

This is idempotent — safe to run multiple times. The Properties Data tab is cleared once on first run, then never cleared again.

## Env File Template

See `.env.example` for all configurable environment variables with comments.

## API Reference

Read `docs/api.md` for full API documentation.

### Bus Fare Data Pipeline

To re-extract bus fare data or troubleshoot problems with it, see bus fare pipeline documentation: `docs/bus-fares.md`.

See `docs/bus-fares.md` for full details on the extraction process, flags,
and how to update the sheet with new fares.
