# Code Discovery Rules

**ALWAYS use the code-review-graph MCP tools for structural code discovery.** A persistent
file watcher (`code-review-graph watch`) keeps the graph up-to-date automatically.

1. Open tool call with `mcp__code_review_graph_query_graph_tool` for:
   - `callers_of` / `callees_of` — find who calls / is called by a function
   - `importers_of` / `imports_of` — find what imports / is imported by a file
   - `children_of` — list members of a class or file
   - `file_summary` — list every symbol in a file with line numbers
   - `inheritors_of` — find subclasses
2. Fall back to `grep`/`read` only for reading file CONTENT after the graph has
   located the exact file and line.
3. NEVER start with `grep`/`read` for cross-file structural questions. The graph
   operates on parsed AST and won't miss patterns that text regex would.

The watcher runs as a persistent background process (`crg-watch`). No manual
`build_or_update_graph` calls needed — it detects filesystem changes automatically.


# AGENTS.md — Houses

**Browser-to-Spreadsheet Ingestion & Enrichment Engine.**

## Quick Start

```bash
make setup && make run          # Install + start dev server
make test                       # Run unit + integration + frontend typecheck/tests
make test-integration           # Integration tests only
```

## Running (AI MUST follow)

- **Always use ``make run``** to start the dev environment. It starts both the
  backend (uvicorn ``--reload`` on :8080) and frontend (Vite on :5173) with
  proper process trapping.
- **NEVER start a dev server via ``bash`` with ``&``** — the process dies when
  bash reaps the background job. The Makefile handles this correctly.
- **NEVER kill the server.** Here's why and what to do instead:

  ### Why you must not touch the server:
  - ``uvicorn --reload`` watches all .py files via inotify. Your code changes are
    live within ~1 second of saving. The server never runs stale code.
  - ``make run`` uses ``trap 'kill 0' EXIT`` — killing one process kills both
    backend and frontend plus the code-review-graph watcher.
  - ``fuser -k 8080/tcp`` kills indiscriminately: the working server, the CRG
    watcher, and any connected browser. It creates daemon crash loops.
  - Launch daemon ``restart: always`` recycles dead servers. Frequent restarts =
    crash loop → needs manual intervention.

  ### What TO do when you need server output:
  - **Read logs**: ``launch logs houses-server --lines=30``
  - **Check if alive**: ``curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/api/health``
  - **Check port**: ``ss -tlnp | grep 8080``

  ### CONSTRAINTS — follow this path when anything seems wrong:

  API error, empty page, or server seems broken?
    - Is ``curl localhost:8080/api/health`` returning 200?
      - YES → the server is live and running your latest code. **Fix your code.**
        Read logs: ``launch logs houses-server --lines=20``
      - NO  → the server may be down. Check port: ``ss -tlnp | grep 8080``
        - Port 8080 has a listener? → **Tell the user** with ``curl`` + ``launch list`` output.
        - Port 8080 is empty?       → **Tell the user** "The dev server is not running."
    - **In every case: do NOT stop, start, or restart anything yourself.**
      The user controls server lifecycle. You report state.

## Decision Tree

- **Develop / test / run**: [docs/development.md](docs/development.md)
- **Architecture overview**: [docs/architecture.md](docs/architecture.md)
- **Add a column**: [docs/column-reference.md](docs/column-reference.md)
- **Add an enrichment module**: [docs/adding-a-new-enrichment-module.md](docs/adding-a-new-enrichment-module.md)
- **Add a DAG node**: [docs/dag-model.md](docs/dag-model.md)
- **Capture / compare the frontend DOM**: Run `tools/capture_dom.py`, then compare against `docs/current-ui/`. See `docs/development.md` → *Capturing the Frontend DOM for Comparison*.
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
| `houses/sheets/` | gspread integration, column schema (`Row`), View tab sync (`View`), formulas |
| `tests/helpers.py` | Reusable fakes: `FakeCommuteRouter`, `FakeEPC`, `make_services()` |
| `houses/nodes/` | New DAG node implementations (replaces old `houses/model/` DAG) |
| `dag/` | DAG library: `Node`, `SourceNode`, `ComputedNode`, `Attempt`, `Provenance` |
| `docs/current-ui/` | Saved reference HTML from the old frontend — compare `capture_dom.py` output against this |
| `tools/capture_dom.py` | Reusable script to capture rendered Vue frontend DOM + screenshot |
