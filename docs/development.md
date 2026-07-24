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

## Code Knowledge Graph

This project uses `code-review-graph` (MCP tool) to build a knowledge graph
of the codebase — functions, classes, files, their relationships, and
community structure. The graph enables structural analysis, impact radius
checks, code review support, and refactoring guidance.

### Incremental Builds Only

**Never run a full rebuild.** The graph supports incremental updates, which
are fast (seconds) and only re-parse changed files. Full rebuilds parse every
file from scratch and are unnecessary.

```bash
# Correct — incremental (default):
uvx code-review-graph build
uvx code-review-graph postprocess

# Never do this:
uvx code-review-graph build --full-rebuild
```

When using the MCP tool `code-review-graph_build_or_update_graph_tool`:
- Leave `full_rebuild` unset or set to `false` (the default)
- Set `postprocess` to `"minimal"` for quick builds that skip
  community/flow detection

### When to Build

- The first time you use the graph in a session
- After code changes — the graph auto-detects what changed
- Check staleness: `uvx code-review-graph status` — if "Built at commit"
  doesn't match HEAD, rebuild

## API Reference

Read `docs/api.md` for full API documentation.

### Bus Fare Data Pipeline

To re-extract bus fare data or troubleshoot problems with it, see bus fare pipeline documentation: `docs/bus-fares.md`.

See `docs/bus-fares.md` for full details on the extraction process, flags,
and how to update the sheet with new fares.

## Fixing Bugs That Produced Wrong Persisted Data

When a code fix changes what a DAG node computes, existing `node_results`
rows in `data/houses.db` still hold the buggy output. The DAG won't
auto-detect staleness from a code change — it only recomputes when a
dependency's timestamp changes.

### Step 1 — Find the affected `node_results`

Use `sqlite3` to inspect which node IDs need clearing. The affected nodes
are the one whose `compute()` method was fixed, plus everything downstream
in its dependency chain. Node IDs follow the pattern
`{rid}/{person}/{label}/{node_type}` (e.g.
`88639800/Lorena/Aldgate/computed_transit`).

Check which properties have rows for the affected node type:

```sql
SELECT DISTINCT SUBSTR(node_id, 1, INSTR(node_id, '/') - 1) AS rid
FROM node_results
WHERE node_id LIKE '%/Lorena/Aldgate/computed_transit';
```

### Step 2 — Delete only the root node, restart, let the cascade propagate

You only need to delete the `node_results` rows for the **fixed node**
(the one whose `compute()` was wrong). After a server restart, that node
becomes pending (no DB row), recomputes with the fix, and its `changed`
signal fires. Each downstream node detects the newer dependency timestamp
via `_is_stale()` and schedules itself in turn.

Find the affected RIDs:

```sql
SELECT DISTINCT SUBSTR(node_id, 1, INSTR(node_id, '/') - 1) AS rid
FROM node_results
WHERE node_id LIKE '%/Lorena/Aldgate/computed_transit';
```

Then delete just the root rows. For each affected RID and node type:

```sql
DELETE FROM node_results
WHERE node_id = '{rid}/Lorena/Aldgate/computed_transit';
```

Bulk delete for all affected properties at once:

```sql
DELETE FROM node_results
WHERE node_id IN (
    SELECT node_id FROM node_results
    WHERE node_id LIKE '%/Lorena/Aldgate/computed_transit'
);
```

Do NOT delete downstream nodes (`commute`, `final_fuel`,
`commute_breakdown`, etc.) — they will recompute when their dep finishes.
Do NOT delete leaf inputs (`walk`, `drive`, `poi`, `person_name`) — they
are not affected by the fix and don't need recompute.

### Step 3 — Trigger a server reload

The nodes load their state from `node_results` during server startup
(bootstrap). If you deleted rows while the server was running, the
in-memory node objects still hold the old results. Trigger a uvicorn
reload so every node re-loads from the cleaned DB:

```bash
touch houses/server.py   # any watched .py file triggers --reload
```

Wait for the reload to finish (check the logs: "Application startup
complete"). On reload, deleted nodes become `pending` and the background
processor recomputes them with the fixed code. The cascade propagates
through the entire dependency chain automatically.

### Verification

Hit the property detail endpoint and check the affected field is no longer
`pending` and shows the corrected value:

```bash
curl -s http://localhost:8080/api/properties/{rid}/detail | python3 -m json.tool
```

Poll if the compute takes time — the background processor may be working
through other properties first.
