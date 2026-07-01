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
make run          # Start backend (:8080) + frontend (:5173) with auto-reload
```

``make run`` starts both the FastAPI backend (uvicorn with ``--reload``) and
the Vite frontend dev server. The frontend proxies ``/api/*`` requests to the
backend on port 8080.

To start only one:
```bash
make frontend-dev   # Vite dev server only (requires backend separately)
make run            # Backend only — add ``DBG=1 make run`` for extra logging
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

## Capturing the Frontend DOM for Comparison

The rendered Vue frontend can be captured for comparison against the saved
reference HTML in ``docs/current-ui/``.

### Prerequisites

Both servers must be running:

```bash
make run                  # Backend on :8080 (with auto-reload)
# In a separate terminal:
cd houses/frontend && npm run dev   # Frontend on :5173
```

### Capture

```bash
.venv/bin/python tools/capture_dom.py            # Both pages
.venv/bin/python tools/capture_dom.py --list-only  # List page only
.venv/bin/python tools/capture_dom.py --detail-only # Detail page only
```

Output lands in ``tools/captures/<session-timestamp>/``:
- ``dom_list.html`` / ``dom_detail.html`` — full rendered HTML
- ``screenshot_list.png`` / ``screenshot_detail.png`` — full-page screenshots

The script reuses a single browser instance across captures, waits for both
servers to be ready, and reports console errors and card count.

### What to Compare

The reference HTML is in ``docs/current-ui/``:
- ``list-page.html`` — old Jinja-rendered list page
- ``detail-page.html`` — old Jinja-rendered detail page

Compare key elements:
- **Address** — should include full postcode, not just outcode
- **Walk-to-town** — first commute row, labelled with town name
- **Commute labels** — from settings (Pimlico, Bracknell, Dad, Aldgate, …)
- **Commute mode** — ``transit``, ``drive``, ``walk`` appended to duration
- **Duration format** — ``1h30`` (no space, no ``m`` suffix), ``9m``
- **Direction links** — every commute/school pill must be a clickable
  ``<a>`` pointing to Google Maps directions
- **School Ofsted pills** — ``pill--sm pill--{good|warn|bad}``
- **School walk pills** — from child (George) commutes, with ``↗`` suffix
- **Card sorting** — by computed score, highest first
- **Total monthly** — at the very bottom of each card

## API Reference

Read `docs/api.md` for full API documentation.

### Bus Fare Data Pipeline

To re-extract bus fare data or troubleshoot problems with it, see bus fare pipeline documentation: `docs/bus-fares.md`.

See `docs/bus-fares.md` for full details on the extraction process, flags,
and how to update the sheet with new fares.
