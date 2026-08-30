# Development Guide

## Setup & Config

```bash
make setup        # Create venv, install deps
```

Config: `pydantic-settings`, `HOUSES_` prefix (see `houses/settings.py` and `.env.example`).

**API keys live in the shell environment** (`.zshrc`, `.bashrc`), NOT in `.env` or code. `.env` is for non-secret defaults only. Never read, log, echo, or store keys in files.

## Running

```bash
make run          # Backend (:8765) + frontend (:5173), auto-reload
make frontend-dev  # Vite dev server only (backend must be running separately)
```

`make run` starts FastAPI (uvicorn `--reload`) + Vite; frontend proxies `/api/*` to :8765. Extra backend logging: `DBG=1 make run`.

**Phone access**: `make run` prints `http://<LAN_IP>.sslip.io:5173` for the phone. The dev server is plain HTTP — do NOT add `server.https` to vite.config.ts: it makes vite HTTPS-ONLY, breaking every http bookmark with `ERR_EMPTY_RESPONSE` (the browser connects, vite doesn't speak HTTP, connection closed). If the phone's browser (HTTPS-First / "Always use secure connections") auto-upgrades and hard-fails, the fix is the browser setting, not the app — HTTPS-First normally falls back to http against a plain-http server. A real trusted-cert HTTPS setup for the dev box needs a reverse proxy (Caddy/nginx); not worth it for dev.

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

`code-review-graph` (MCP tool) builds a knowledge graph of the codebase; enables structural analysis, impact radius, review support, refactoring guidance. The `crg-watch` hub service watches the repo and keeps the graph converged automatically — including across branch switches: a checkout is just file events, and the watcher re-parses additions and deletions and updates the recorded branch/commit within seconds.

**Never full rebuild** — incremental updates re-parse only changed files. When using the MCP `build_or_update_graph_tool`: leave `full_rebuild` unset/false; `postprocess: "minimal"` for quick builds.

**Branch switches are free while the watcher runs.** If it was down during a switch (boot, crash), the graph is silently stale, and restarting the watcher does NOT recover it — the watcher is purely event-driven, with no startup sweep. Detect: `uvx code-review-graph status` shows "Built at commit" ≠ HEAD or a branch WARNING. Recover: `uvx code-review-graph build` — incremental; re-parses only the files the switch changed.

## Bus Fare Data Pipeline

Extraction/troubleshooting: `docs/bus-fares.md` (extraction process, flags).

## Fixing Bugs — Write the Contract Test First

Before changing any code for a bug, write the regression test. The rule:

> **Test the contract the user relies on, not the mechanism you're about to change.** The test must fail today, pass after your fix, and still fail if the same symptom regresses through a *different* mechanism.

A test that only passes because of the specific lines you changed is a test of your edit, not of the bug.

Apply it:

1. **State the symptom as an invariant** in the user's language — "the current-house dropdown lists the family's current homes", "the commute shows on the property card".
2. **Derive assertions from the symptom, not the code.** Reading the buggy code first biases you toward testing the mechanism; the bug report is what tells you the contract.
3. **Assert outcomes, not internals.** Assert the commute is *available on the card*, not that `RailFareNode` returned `Attempt.succeeded`.
4. **Test both sides of an interaction.** Where two things can shadow each other (literal vs parameterised routes, toggle on/off, serialization keys), pin both states — a precedence bug in either direction must fail loudly.
5. **Watch it fail for the right reason**, fix the mechanism, then re-check: *would this test also catch a different mechanism producing the same symptom?* If not, widen it before committing.

## Fixing Bugs That Produced Wrong Persisted Data

A code fix changes what a node computes, but existing `node_results` rows still hold buggy output. **The DAG only recomputes when a dep's timestamp changes — it doesn't detect code changes.** So after a fix you must force the affected nodes to recompute.

### The mechanism — `POST /api/admin/regenerate`

Superuser-only. Body is a list of node-id **patterns** where `*` matches any run of characters (including `/`); a pattern without `*` is an exact id. The matched nodes recompute through the normal refresh path (persist + signals), and the scheduler cascade is drained before the response returns — downstream nodes recompute automatically, no restart needed.

```bash
COOKIE="$(cat /tmp/cookie.txt)"   # a superuser session
curl -b "session=$COOKIE" -X POST http://localhost:8765/api/admin/regenerate \
  -H 'Content-Type: application/json' \
  -d '{"patterns": ["*/computed_transit"]}'
# → {"matched": 40, "regenerated": [{"node": ".../computed_transit", "status": "succeeded"}, ...], "skipped": []}
```

- **Find the node ids**: `{rid}/{person}/{label}/{node_type}` (e.g. `88639800/Lorena/Aldgate/computed_transit`). The affected nodes = the fixed node + everything downstream — but you only need to list the **root** of each affected chain; the cascade handles the rest. `*`-patterns cover all properties in one string (`["*/council_tax"]`).
- **Matched input nodes** (no computation) are reported in `skipped`, never silently dropped.
- **Example**: the A3 council-tax fallback changed `CouncilTaxNode.compute`, but every property's persisted result stayed `impossible` (fresh-by-timestamp, wrong). One call — `{"patterns": ["*/council_tax"]}` — regenerated all 40 nodes and unblocked 16 downstream totals.

### When the endpoint doesn't fit

- **Non-superuser environment** (a fresh checkout, no admin session): you can fall back to the old approach — delete the affected `node_results` rows for the fixed node only, then `touch houses/server.py` to trigger `--reload`; deleted nodes become pending and recompute at startup. This is manual and easy to over-delete, so prefer the endpoint.
- **Permanently new semantics**: if a compute change makes old results *meaningless* (not just wrong), bump the node id (`"{rid}/town_desc_v2"`) instead — see `docs/dag-library.md`.

### Verification

```bash
curl -s http://localhost:8765/api/properties/{rid}/detail | python3 -m json.tool
```

Affected field should show the corrected value and `status: succeeded`. Poll if compute takes time — the processor may be working through other properties.
