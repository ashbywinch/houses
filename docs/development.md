# Development Guide

> **Production Sheet Access** — use existing scripts/endpoints to modify the production sheet. If existing tools can't do it, extend them or build a well-designed new tool.

## Setup & Config

```bash
make setup        # Create venv, install deps
```

Config: `pydantic-settings`, `HOUSES_` prefix (see `houses/config.py` and `.env.example`).

**API keys live in the shell environment** (`.zshrc`, `.bashrc`), NOT in `.env` or code. `.env` is for non-secret defaults only. Never read, log, echo, or store keys in files.

## Running

```bash
make run          # Backend (:8765) + frontend (:5173), auto-reload
make frontend-dev  # Vite dev server only (backend must be running separately)
```

`make run` starts FastAPI (uvicorn `--reload`) + Vite; frontend proxies `/api/*` to :8765. Extra backend logging: `DBG=1 make run`.

## Testing

```bash
make test                 # Unit + integration (fast, mocked, no external calls)
make test-integration     # Integration only
make coverage             # Test with coverage report
```

Test layout mirrors the module tree under `tests/` (`houses/nodes/area.py` → `tests/unit/nodes/test_area.py`). DI patterns and fake usage: `docs/coding-standards.md` → *Dependency Injection* and `docs/testing-standards.md`.

**Markers:** no marker = unit (fast, no external); `@pytest.mark.integration` = full pipeline (excluded from `make test`).

## Lint & Format

```bash
make lint         # ruff + frontend CSS lint + basedpyright (via make test)
make format       # Auto-fix formatting
```

`pyproject.toml` holds the tool config (line length, Python target).

## Sheet Setup

```bash
uv run python scripts/setup_sheet.py
```

Idempotent. Creates Properties Data + Properties View tabs. Data tab cleared once on first run, never again.

## Capturing the Frontend DOM

Compare rendered Vue against saved reference HTML in `docs/current-ui/`.

**Prereqs:** both servers running (`make run` + `cd houses/frontend && npm run dev`).

The frontend is behind Google OAuth. One-time login uses Google's OAuth device flow — the script prints a code, you approve from any device (your browser's saved credentials + 2FA), and it saves the session. No browser automation, no credentials on this machine:

```bash
.venv/bin/python tools/capture_dom.py --login
```

Then capture as usual:

```bash
.venv/bin/python tools/capture_dom.py            # both pages
.venv/bin/python tools/capture_dom.py --list-only
.venv/bin/python tools/capture_dom.py --detail-only
```

Output lands in `tools/captures/<session-timestamp>/`. The script reuses one browser instance, waits for both servers, reports console errors and card count.

The session cookie lasts 30 days. On expiry the tool fails with a "Session expired or invalid" hint — re-run `--login`. `tools/.auth-state.json` holds a live session cookie: **never commit it** (gitignored).

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

`code-review-graph` (MCP tool) builds a knowledge graph of the codebase; enables structural analysis, impact radius, review support, refactoring guidance.

**Never full rebuild** — incremental updates re-parse only changed files. When using the MCP `build_or_update_graph_tool`: leave `full_rebuild` unset/false; `postprocess: "minimal"` for quick builds.

**When to build:** first graph use in a session; after code changes; or when `uvx code-review-graph status` shows "Built at commit" ≠ HEAD.

## Bus Fare Data Pipeline

Extraction/troubleshooting: `docs/bus-fares.md` (extraction process, flags, sheet update).

## Fixing Bugs That Produced Wrong Persisted Data

A code fix changes what a node computes, but existing `node_results` rows still hold buggy output. **The DAG only recomputes when a dep's timestamp changes — it doesn't detect code changes.**

### Step 1 — Find affected `node_results`

Node IDs: `{rid}/{person}/{label}/{node_type}` (e.g. `88639800/Lorena/Aldgate/computed_transit`). The affected nodes = the fixed node + everything downstream. Example:

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
curl -s http://localhost:8765/api/properties/{rid}/detail | python3 -m json.tool
```

Affected field should no longer be `pending` and show the corrected value. Poll if compute takes time — the processor may be working through other properties.
