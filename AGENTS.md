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
make setup && make run          # Install + start dev server (backgrounded)
make test                       # Run unit + integration + frontend typecheck/tests
make test-integration           # Integration tests only
make stop                       # Stop dev server + frontend
```

## Running (AI MUST follow)

- **Always use ``make run``** to start the dev environment.
- **Logs**: ``cat .logs/backend.log | tail -n 30``
- **Stop**: ``make stop``
- **Server errors?** Read the logs. ``uvicorn --reload`` picks up code changes in ~1s. See ``never-touch-server`` rule.
  ``uvicorn --reload`` watches all .py files. Code changes are live in ~1s.
- **Never** restart or kill the server. ``reload`` handles it.  Read logs instead.
- **Never** ``fuser -k 8080/tcp`` — kills the CRG watcher.
- **Never** ``hub restart`` anything — pointless with --reload.
- **Health check** that hangs? Stop running it. Read logs.

## Database

- **NEVER delete or recreate the database.** Here's why and what to do instead:

  ### Why you must not touch the database:
  - The DB is backed by ``node_results``, ``source_values``, and other tables
    that persist property data. Deleting it erases real data irrecoverably.
  - Every property's DAG state, commute results, EPC data, and user settings
    live in the DB. Re-seeding from the sheet only restores raw addresses —
    computed data is gone.
  - Test failures, server errors, and unexpected behavior are **always** caused
    by code bugs, never by a "corrupted" or "stale" database.
  - The DB file is at ``data/houses.db`` (SQLite). Deleting ``data/`` or running
    ``rm data/houses.db`` destroys everything.

  ### What TO do when data seems wrong:
  - **Inspect the DB directly**: ``sqlite3 data/houses.db "SELECT * FROM node_results LIMIT 5;"``
  - **Check a property's DAG state**: query ``node_results`` for a specific RID
  - **Re-run a specific computation**: call the relevant API endpoint, don't nuke the DB
  - **Fix a bug that produced wrong persisted data**: see [docs/development.md](docs/development.md) → *Fixing Bugs That Produced Wrong Persisted Data*
  - **Write a test that reproduces the bug** — if the test passes against a fresh DB

  ### CONSTRAINTS:
  - **Do NOT** run ``rm``, ``unlink``, ``truncate``, ``DROP TABLE``, or any
    command that deletes database files or tables.
  - **Do NOT** delete ``data/``, ``data/houses.db``, or any file under ``data/``.
  - **Settings writes from scripts are guarded**: pushing to the settings
    nodes (persons/financial/thresholds) from anything but the running app
    or pytest raises ``RuntimeError`` unless ``HOUSES_SCRIPTS_MAY_WRITE=1``
    is set — a deliberate data-fix script must set it explicitly. REPL/adhoc
    kernels that skip pytest isolation cannot silently replace family data.
    Prefer ``dataclasses.replace`` over rebuilding records: the settings
    PATCH endpoint merges (unmentioned fields are preserved), and scripts
    must do the same.
  - **If you believe the DB is corrupted**: reproduce the issue in a test against
    a clean in-memory SQLite database. If it reproduces, it's a code bug. If it
    doesn't, the real DB has data your code doesn't handle — fix the code.

## Decision Tree

- **Develop / test / run**: [docs/development.md](docs/development.md)
- **Coding standards / conventions**: [docs/coding-standards.md](docs/coding-standards.md)
- **Writing tests**: [docs/testing-standards.md](docs/testing-standards.md)
- **Architecture overview**: [docs/architecture.md](docs/architecture.md)
- **Add a column**: [docs/column-reference.md](docs/column-reference.md)
- **Add an enrichment module**: [docs/adding-a-new-enrichment-module.md](docs/adding-a-new-enrichment-module.md)
- **Enrichment modules (index)**: [docs/enrichment-modules.md](docs/enrichment-modules.md)
- **Bus fares (TfL stop fares, zones, extraction)**: [docs/bus-fares.md](docs/bus-fares.md)
- **DAG library & adding a node**: [docs/dag-library.md](docs/dag-library.md)
- **Isochrone website integration (settings page, generation, map)**: [docs/website-isochrone-integration.md](docs/website-isochrone-integration.md)
- **Capture / compare the frontend DOM**: Run `tools/capture_dom.py`, then compare against `docs/current-ui/`. See `docs/development.md` → *Capturing the Frontend DOM*.
- **Commute data (isochrones, shed, maps)**: [docs/commute.md](docs/commute.md)
- **Rightmove commute monitor**: [docs/rightmove-commute-monitor.md](docs/rightmove-commute-monitor.md)
- **Write docs**: [docs/writing-documentation.md](docs/writing-documentation.md)
- **Deploy to Oracle Cloud Free Tier**: [docs/deployment-oracle-free-tier.md](docs/deployment-oracle-free-tier.md)
- **Lucidlint review log (accepted/deferred findings)**: [docs/lucidlint-review-log.md](docs/lucidlint-review-log.md)
- **Use the API**: [docs/api.md](docs/api.md)
- **Remaining work (uncertainty in the DAG library, usability backlog)**: [docs/remaining-work-plan.md](docs/remaining-work-plan.md)
- **Troubleshoot batch endpoints**: [docs/troubleshooting-endpoints.md](docs/troubleshooting-endpoints.md)
- **Users & UX requirements (provenance, filters, states)**: [docs/personas.md](docs/personas.md)
- **Frontend architecture decisions (Vue)**: [docs/vue-architecture-decisions.md](docs/vue-architecture-decisions.md)

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
| `dag/` | DAG library: `Node`, `DerivedNode`, `UserInputNode`, `Attempt`, `Provenance` — see [docs/dag-library.md](docs/dag-library.md) |
| `docs/current-ui/` | Saved reference HTML from the old frontend — compare `capture_dom.py` output against this |
| `tools/capture_dom.py` | Reusable script to capture rendered Vue frontend DOM + screenshot |

