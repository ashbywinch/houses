# Development Guide

> **Production Sheet Access** — use existing scripts/endpoints to modify the production sheet. If existing tools can't do it, extend them or build a well-designed new tool.

## Setup & Config

```bash
make setup        # Create venv, install deps (pytest, ruff, coverage via uv)
```

Config: `pydantic-settings`, `HOUSES_` prefix.

**API keys live in the shell environment** (`.zshrc`, `.bashrc`), NOT in `.env` or code. `.env` is for non-secret defaults only. Never read, log, echo, or store keys in files.

All env vars can go in `.env` (non-sensitive only). Template: `.env.example`.

## Running

```bash
make run          # Backend (:8080) + frontend (:5173), auto-reload
make frontend-dev  # Vite dev server only (backend must be running separately)
```

`make run` starts FastAPI (uvicorn `--reload`) + Vite. Frontend proxies `/api/*` to :8080. Extra backend logging: `DBG=1 make run`.

## Testing

```bash
make test                 # Unit + integration (fast, mocked, no external calls)
make test-integration     # Integration only
make coverage             # Test with coverage report
```

### Layout

```
tests/
├── helpers.py               # Reusable fakes + make_services() factory
├── conftest.py
├── unit/                    # Pure functions, _kwarg injection
│   ├── test_routing.py
│   ├── test_enricher.py
│   └── ...
├── integration/             # Full pipeline with fakes or MockTransport
│   ├── test_server.py       # HTTP endpoint tests (TestClient)
│   └── conftest.py          # MockTransport, cache isolation
└── e2e/                     # Real API calls (skipped by default)
```

### DI patterns

See `docs/coding-standards.md` → *Dependency Injection*:

| Pattern | When |
|---------|------|
| `Services` container | Replace an entire enrichment module |
| `ContextVar` | Per-request state (bus fares, sheets client) |
| `_kwarg` | Pass a specific data object to a leaf function |

**Markers:** no marker = unit (fast, no external); `@pytest.mark.integration` = full pipeline (excluded from `make test`).

## Lint & Format

```bash
make lint         # ruff check houses/ tests/ + frontend CSS lint
make format       # Auto-fix formatting
```

`pyproject.toml`: line length 120, target Python 3.12.

## Sheet Setup

```bash
uv run python scripts/setup_sheet.py
```

Idempotent. Creates Properties Data + Properties View tabs. Data tab cleared once on first run, never again.

## Capturing the Frontend DOM

Compare rendered Vue against saved reference HTML in `docs/current-ui/`.

### Prereqs: both servers running

```bash
make run                  # backend :8080
cd houses/frontend && npm run dev   # frontend :5173 (separate terminal)
```

### Capture

```bash
.venv/bin/python tools/capture_dom.py            # both pages
.venv/bin/python tools/capture_dom.py --list-only
.venv/bin/python tools/capture_dom.py --detail-only
```

Output → `tools/captures/<session-timestamp>/`: `dom_list.html`/`dom_detail.html` + full-page screenshots. Script reuses one browser instance, waits for both servers, reports console errors and card count.

### What to compare (vs `docs/current-ui/`)

- **Address** — full postcode, not just outcode
- **Walk-to-town** — first commute row, labelled with town name
- **Commute labels** — from settings (Pimlico, Bracknell, Dad, Aldgate, …)
- **Commute mode** — `transit`/`drive`/`walk` appended to duration
- **Duration format** — `1h30` (no space, no `m` suffix), `9m`
- **Direction links** — every commute/school pill is a clickable `<a>` to Google Maps directions
- **School Ofsted pills** — `pill--sm pill--{good|warn|bad}`
- **School walk pills** — from child (George) commutes, `↗` suffix
- **Card sorting** — by computed score, highest first
- **Total monthly** — very bottom of each card

## Code Knowledge Graph

`code-review-graph` (MCP tool) builds a knowledge graph: functions, classes, files, relationships, community structure. Enables structural analysis, impact radius, review support, refactoring guidance.

### Incremental builds only

**Never full rebuild.** Incremental updates re-parse only changed files (seconds).

```bash
# ✓ incremental (default)
uvx code-review-graph build
uvx code-review-graph postprocess
# ✗ never
uvx code-review-graph build --full-rebuild
```

MCP `build_or_update_graph_tool`: leave `full_rebuild` unset/false; `postprocess: "minimal"` for quick builds skipping community/flow detection.

### When to build

- First graph use in a session
- After code changes (auto-detects changes)
- Staleness check: `uvx code-review-graph status` — if "Built at commit" ≠ HEAD, rebuild

## Bus Fare Data Pipeline

Extraction/troubleshooting: `docs/bus-fares.md` (extraction process, flags, sheet update).

## Fixing Bugs That Produced Wrong Persisted Data

A code fix changes what a node computes, but existing `node_results` rows still hold buggy output. **The DAG only recomputes when a dep's timestamp changes — it doesn't detect code changes.**

### Step 1 — Find affected `node_results`

Node IDs: `{rid}/{person}/{label}/{node_type}` (e.g. `88639800/Lorena/Aldgate/computed_transit`). The affected nodes = the fixed node + everything downstream.

```sql
SELECT DISTINCT SUBSTR(node_id, 1, INSTR(node_id, '/') - 1) AS rid
FROM node_results
WHERE node_id LIKE '%/Lorena/Aldgate/computed_transit';
```

### Step 2 — Delete ONLY the root node; restart; cascade propagates

Delete rows for the **fixed node** only. After restart it becomes pending (no DB row), recomputes with the fix, fires `changed`; downstream detects the newer timestamp via `_is_stale()` and schedules itself.

```sql
-- per RID + node type
DELETE FROM node_results WHERE node_id = '{rid}/Lorena/Aldgate/computed_transit';
-- all affected properties at once
DELETE FROM node_results
WHERE node_id IN (
    SELECT node_id FROM node_results
    WHERE node_id LIKE '%/Lorena/Aldgate/computed_transit'
);
```

**Do NOT delete** downstream nodes (`commute`, `final_fuel`, `commute_breakdown`) — they recompute when their dep finishes. **Do NOT delete** leaf inputs (`walk`, `drive`, `poi`, `person_name`) — unaffected by the fix.

### Step 3 — Trigger server reload

Nodes load state from `node_results` at startup. If you deleted rows while the server runs, in-memory nodes still hold old results:

```bash
touch houses/server.py   # any watched .py triggers --reload
```

Wait for "Application startup complete" in logs. Deleted nodes become pending; the background processor recomputes; cascade propagates.

### Verification

```bash
curl -s http://localhost:8080/api/properties/{rid}/detail | python3 -m json.tool
```

Affected field should no longer be `pending` and show the corrected value. Poll if compute takes time — the processor may be working through other properties.
