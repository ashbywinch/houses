---
name: code-review-and-refactor
description: |
  Review code against the project's coding standards, identify issues, and
  use code-review-graph MCP tools to perform safe, preview-then-apply
  refactorings without changing functionality.
---

Use the companion rule (`.kilo/rules/code-quality.md`) to decide **when** to invoke this skill. Use this skill to decide **how** to carry it out.

## Required Tools

This skill requires two tools. Check both upfront — each takes under a second.

```bash
uvx code-review-graph --help >/dev/null 2>&1 && echo "crg=ok" || echo "crg=missing"
uv run python -c "import rope" 2>/dev/null && echo "rope=ok" || echo "rope=missing"
```

- **Missing `code-review-graph`:** `uvx` auto-installs it on first run.
- **Missing `rope`:** run `uv pip install rope`.

The `code-review-graph` MCP server is already configured in `kilo.jsonc` — no action needed there.

Once tools are confirmed, ensure the graph is built:

1. Call `list_graph_stats_tool`. If it returns an error or zero nodes, call `build_or_update_graph_tool` followed by `run_postprocess_tool`.
2. If the graph exists but code has changed, call `build_or_update_graph_tool` (incremental update).

## Phase 0: Git Hygiene

**Must start on a clean working tree on a feature branch.**

1. `git status --porcelain` must produce no output. If it doesn't, refuse: "Working tree is not clean. Commit, stash, or discard changes first."
2. If on `main`/`master` or detached HEAD, auto-create a branch named `refactor/code-quality-<timestamp>` with `date +%s`. Do not ask the user.

## Step 1: Analyse

Run these queries in order:

| Tool | What it reveals | Purpose |
|------|-----------------|---------|
| `get_architecture_overview_tool` | Communities and coupling warnings | Identifies entangled modules |
| `get_hub_nodes_tool` | Functions with the most connections | Finds over-coupled modules |
| `get_bridge_nodes_tool` | Functions that are single points of failure | Finds architectural chokepoints |
| `get_surprising_connections_tool` | Cross-community calls | Finds misplaced responsibility |
| `find_large_functions_tool` `min_lines=50` | Files over 50 lines | Finds extraction candidates |
| `get_suggested_questions_tool` | Untested hotspots, critical bridges | Cross-check against own findings |
| `refactor_tool(mode="suggest")` | Unused functions, renaming opportunities | Identifies quick wins |
| `refactor_tool(mode="dead_code")` | Functions with no callers | Identifies safe removals |

## Step 2: Load Coding Standards

Read `docs/coding-standards.md`. Skip if already in context.

## Step 3: Review — Check Code Against Standards

Examine the code flagged in Step 1 against the standards. Look for:

- **Module encapsulation violations**: Private symbols imported across modules (`_geocode`, `_rightmove_id`, `_KNOWN_COUNTIES`), imports inside function bodies
- **Module boundary violations**: Classes with non-trivial behavior mixed into `models.py`, classes not in their own module
- **Missing member functions**: Serialization, computed properties that live in a separate module instead of on the class
- **Data clumps**: The same parameters passed together across multiple functions (`lat, lng` as separate args), the same dict keys constructed in multiple places (`{"walk_to_town_minutes", "amenities"}`), the same long parameter lists repeated (7-arg `_run_backfill_enrichment`). Promote to a class named after the domain concept.
- **Naming smells**: Verb-suffixed class names (Orchestrator, Manager, Handler), "utils" modules, functions named like classes, variables named after their type rather than their domain concept. If you are unsure what the correct domain concept is for a class, method, or variable — stop and ask the user. Guessing a domain name wrong is worse than leaving a bad name; the user knows their problem domain.
- **Type smells**: `Any`, wide unions, `| None` cop-outs, nested `dict` deep in business logic
- **Structure smells**: Mixed concerns, deep nesting, cross-module duplication (haversine, geocoding, `_APIState`)
- **Fail-fast violations**: `except: pass`, unconditional fallbacks, `# type: ignore` without explanation
- **Functional style violations**: In-place mutation of arguments, side effects mixed with computation
- **Async hygiene**: Sync I/O in async functions
- **Bounded caches**: Unbounded in-memory dicts (`_geo_cache`, `_town_cache`)
- **Config-in-JSON violations**: Magic numbers hardcoded in modules instead of in a config file
- **Dead code**: Unused functions, models, scripts
- **Single-source-of-truth violations**: Same mapping in multiple files (column→field maps, formulas)
- **Mystery code**: Magic numbers, raw column indices, inline comments that should be function names
- **Long files**: >500 line modules
- **Testing violations**: Mock call-pattern assertions, Python-level mocking of internals, duplicated mock boilerplate, `settings` mutation with try/finally, sleep-based timing, silent e2e passes, redundant tests (multiple tests covering the same code path with the same assertions)

Group by severity:
- **🔴 Must fix**: Hard rule violation (empty except, `global` keyword, magic numbers, `# type: ignore` without explanation, private imports across modules, blocking the event loop, data clumps — these indicate a missing domain concept)
- **🟡 Should fix**: Principle violation (naming, structure, type expressiveness, functional style, module boundaries, cache discipline, dead code)
- **🔵 Could fix**: Minor improvement (slightly long function, cosmetic naming)

## Step 4: Pick ONE Goal

From the findings, select exactly one coherent goal for the PR. A good goal satisfies all of:

1. **One axis of change** — purely structural (moving code, extracting functions), purely cosmetic (renaming), or purely removal (dead code). Do not mix categories in one PR.
2. **1–4 files touched** — the entire change stays within a small group of closely related files. If it touches 5+ files, it's too broad; split it.
3. **Tests pass without modification** — if the refactoring requires test changes, the scope has leaked beyond pure refactoring. Pick a smaller goal.  
   **Exception:** a test may mock at the wrong layer (Python-level `patch` instead of HTTP transport mock) and still try to verify real behavior underneath. In that case the test is valuable but brittle — you may fix the test to mock at the correct boundary as part of the refactoring, but only if the test truly exercises a real functional requirement. If the test only asserts call patterns (`mock_fn.assert_called_once_with(args)`) with no behavioral assertion, it adds no value — delete it.  
   **Do not keep redundant tests.** Two tests that cover the same code path with the same assertions are waste. One test per functional requirement, one assertion category per test.
4. **Measurable improvement** — you can state before/after: "removed 3 copies of `_haversine_km`", "removed 2 private-symbol imports", "eliminated 4 dead functions in `enricher.py`".
5. **The user can understand it from the PR title alone** — "Extract shared haversine formula into geo_utils.py" is good. "Clean up enricher.py" is vague.

Prioritisation:

1. **🔴 Must-fix items in hub modules** — hub modules affect the most code. Fixing a private-symbol import in `server.py` (84 connections) propagates to everything that reads server.py.
2. **🔴 Must-fix items in any module** — hard rule violations (blocking the event loop, empty except).
3. **🟡 Items that span multiple files** — cross-module duplication (3 copies of haversine in 3 files), single-source-of-truth violations (column→field maps in two places). These are high-value because a single change fixes multiple violations.
4. **🟡 Items within a single file** — naming, types, long functions. Bounded, lower risk.
5. **🔵 Items** — cosmetic improvements. Only if nothing else is available.

**Do not pick more than one goal.** This skill makes one improvement per invocation. The user runs it again if more work is needed.

### Present the Goal for Approval

Before executing anything, present the selected goal to the user:

> "Proposed goal: [one-line description, e.g. Extract 3 copies of haversine formula into shared GeoPoint.haversine_km]
> Files affected: [list, e.g. enricher.py, walkability.py, rail_fares.py, geo.py (new)]
> Violations addressed: [which standards violations this fixes]
> Expected improvement: [measurable before/after]
>
> Proceed? (y/n)"

Wait for the user's response. If they approve, continue. If they reject or suggest a different goal, go back to Step 4 and pick again.

## Step 5: Execute the Goal

**CRITICAL: Every refactoring must preserve existing functionality exactly. If a change alters runtime behavior, reject it.**

**If you discover a bug** — a clear code path that produces incorrect results — do not fix it. Stop, tell the user what you found and where, and ask what they want to do. Bug fixes follow a different process (test-first) and are outside the scope of this skill.

Choose the tool based on the operation:

| Operation | Tool | How |
|---|---|---|
| **Rename** symbol across files | `code-review-graph` `refactor_tool(mode="rename")` | MCP — preview, then `apply_refactor_tool` |
| **Extract** function/variable | **Rope** via inline Python | Write a `uv run python` script |
| **Move** symbol to another module | **Rope** via inline Python | Write a `uv run python` script |
| **Remove** dead code | MCP to find, then manual delete | Detection via `refactor_tool(mode="dead_code")` |
| **Organize imports** | **Rope** `rope.refactor.importutils` | Write a `uv run python` script |
| **Change signature** | **Rope** `rope.refactor.change_signature` | Write a `uv run python` script |
| **Restructure** (AST pattern) | **Rope** `rope.refactor.restructure` | Write a `uv run python` script |

**Rope usage pattern:**
```python
from rope.base.project import Project
from rope.base.libutils import path_to_resource
from rope.refactor.extract import ExtractMethod
project = Project(".")
resource = path_to_resource(project, "houses/enricher.py")
refactoring = ExtractMethod(project, resource, start_offset, end_offset)
changes = refactoring.get_changes("new_name")
print(changes.get_description())   # preview
project.do(changes)                # apply
project.history.undo()             # undo if needed
project.close()
```

For offset calculation: `resource.read().find("def target_name")`.

**Rope limitation:** rename can fail with "Not a resolvable python identifier selected" for some symbols. Try a different offset (point to `def`, not the name) or use `code-review-graph`'s rename tool.

**Verification (mandatory):**
- Run `make test` — all tests pass
- Run `make lint` — no new errors
- Visually inspect the changed code with Read
- `git commit -am "refactor: <what changed and why>"`

**If neither tool can handle the change** (e.g., splitting a module), fall back to manual editing with extra checks:
- Run `detect_changes_tool` before and after — blast radius unchanged
- Read the full diff with `git diff`
- Run `make test`
- If in doubt, don't make the change

## Step 6: Measure — Rebuild Graph and Compare

After the commit, rebuild and re-run key metrics:

```
build_or_update_graph_tool
run_postprocess_tool
list_graph_stats_tool
get_architecture_overview_tool
get_hub_nodes_tool
get_bridge_nodes_tool
get_suggested_questions_tool
detect_changes_tool
```

Compare against the pre-change state from Step 1:
- Did hubs lose connections?
- Did bridge betweenness decrease?
- Did coupling between communities decrease?
- Did the risk score drop?

If a metric got worse, the refactoring may have increased coupling — `git reset HEAD~1 --hard` and reconsider.

## Step 7: Create a Pull Request

Create a PR with a summary of what was changed and why. Reference the specific violations addressed. If the measure step showed improvements, include those numbers.

## Interaction With Other Rules

- **test-first.md** — Governs the process of when to write tests (TDD). Testing quality principles (mock only the boundary, test behavior not implementation, no sleep-based timing) are in `docs/coding-standards.md` under the Testing section.
- **code-quality.md** — This rule. The review phases above cover testing violations. When reviewing a test file, pay particular attention to mock boundaries, boilerplate duplication, and invariant coverage.
- **use-existing-tools.md** — If you discover a need to inspect sheet data, use `/dump-sheet` from `.kilo/command/` rather than writing inline Python.
- **fail-fast.md** — This rule's principles are part of the review criteria. If the review discovers fail-fast violations, they should be flagged.
