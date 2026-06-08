---
name: code-review-and-refactor
description: |
  Review code against the project's coding standards, identify issues, and
  perform safe, preview-then-apply refactorings without changing functionality.
---

## When To Use

The companion rule (`.kilo/rules/code-quality.md`) decides **when** to invoke this skill. This skill decides **how** to carry it out.

## Required Tools

Check both upfront — each takes under a second.

```bash
uvx code-review-graph --help >/dev/null 2>&1 && echo "crg=ok" || echo "crg=missing"
uv run python -c "import rope" 2>/dev/null && echo "rope=ok" || echo "rope=missing"
```

- **Missing `code-review-graph`:** `uvx` auto-installs it on first run.
- **Missing `rope`:** run `uv pip install rope`.

## Phase 0: Git Hygiene

**Must start on a clean working tree on a feature branch.**

1. `git status --porcelain` must produce no output. If it doesn't, refuse: "Working tree is not clean. Commit, stash, or discard changes first."
2. If on `main`/`master` or detached HEAD, auto-create a branch named `refactor/code-quality-<timestamp>` with `date +%s`. Do not ask the user.

## Phase 1: Build / Check Graph

1. `uvx code-review-graph status 2>&1` — if "Nodes: 0" or error, run `uvx code-review-graph build` + `uvx code-review-graph postprocess`.
2. If graph exists but code has changed (current commit differs from "Built at commit" in status output), run `uvx code-review-graph build` (incremental) + `uvx code-review-graph postprocess`.

## Step 1: Analyse

Run these analyses to find candidates for refactoring:

| Command / Tool | What it reveals | Purpose |
|---|---|---|
| `uvx code-review-graph wiki` then read `wiki/index.md` and each community page | Communities, module sizes, member lists | Identifies hub modules, module boundaries, cohesion |
| `uvx code-review-graph detect-changes --base origin/main` | Risk score, changed functions, affected flows, test gaps | Finds high-risk change areas |
| `find houses -name '*.py' \| xargs wc -l \| sort -rn` | Files > 500 lines | Long-file smells |
| `vulture houses/` (if available) or `grep -rn '^def ' houses/ \| cut -d: -f1 \| sort -u'` then cross-ref usage | Unused functions/modules | Dead code candidates |
| Read the dependency tables in each wiki community page | "Incoming" count = hub status, cross-file dependencies | Over-coupled modules |
| `grep -rn '^def _' houses/ \| grep -v __init__ \| grep -v test_` | Private functions in each module | Module encapsulation check |

Additionally, look for these patterns by reading key files:
- **Data clumps**: Same 2+ parameters passed together (e.g. `lat, lng` as separate args, same dict keys in multiple places)
- **Cross-module duplication**: Same function name or logic in multiple modules
- **Import violations**: `grep -rn 'from houses\.' houses/*.py` for private-import checks
- **Magic numbers**: Hardcoded config values in business logic

## Step 2: Load Coding Standards

Read `docs/coding-standards.md`. Skip if already in context.

## Step 3: Review — Check Code Against Standards

Examine the code flagged in Step 1 against the standards. Look for:

- **Module encapsulation violations**: Private symbols imported across modules (`_geocode`, `_rightmove_id`, `_KNOWN_COUNTIES`), imports inside function bodies
- **Module boundary violations**: Classes with non-trivial behavior mixed into `models.py`, classes not in their own module
- **Missing member functions**: Serialization, computed properties that live in a separate module instead of on the class
- **Data clumps**: The same parameters passed together across multiple functions (`lat, lng` as separate args), the same dict keys constructed in multiple places (`{"walk_to_town_minutes", "amenities"}`), the same long parameter lists repeated. Promote to a class named after the domain concept.
- **Naming smells**: Verb-suffixed class names (Orchestrator, Manager, Handler), "utils" modules, functions named like classes, variables named after their type rather than their domain concept. If you are unsure what the correct domain concept is, stop and ask the user.
- **Type smells**: `Any`, wide unions, `| None` cop-outs, nested `dict` deep in business logic
- **Structure smells**: Mixed concerns, deep nesting, cross-module duplication
- **Fail-fast violations**: `except: pass`, unconditional fallbacks, `# type: ignore` without explanation
- **Functional style violations**: In-place mutation of arguments, side effects mixed with computation
- **Async hygiene**: Sync I/O in async functions
- **Bounded caches**: Unbounded in-memory dicts (`_geo_cache`, `_town_cache`)
- **Config-in-JSON violations**: Magic numbers hardcoded in modules instead of in a config file
- **Dead code**: Unused functions, models, scripts
- **Single-source-of-truth violations**: Same mapping in multiple files
- **Mystery code**: Magic numbers, raw column indices, inline comments that should be function names
- **Long files**: >500 line modules
- **Testing violations**: Mock call-pattern assertions, Python-level mocking of internals, duplicated mock boilerplate, `settings` mutation with try/finally, sleep-based timing, silent e2e passes, redundant tests

Group by severity:
- **🔴 Must fix**: Hard rule violation (empty except, `global` keyword, magic numbers, `# type: ignore` without explanation, private imports across modules, blocking the event loop, data clumps)
- **🟡 Should fix**: Principle violation (naming, structure, type expressiveness, functional style, module boundaries, cache discipline, dead code)
- **🔵 Could fix**: Minor improvement (slightly long function, cosmetic naming)

## Step 4: Pick ONE Goal

From the findings, select exactly one coherent goal for the PR. A good goal satisfies all of:

1. **One axis of change** — purely structural (moving code, extracting functions), purely cosmetic (renaming), or purely removal (dead code). Do not mix categories in one PR.
2. **1–4 files touched** — the entire change stays within a small group of closely related files. If it touches 5+ files, it's too broad; split it.
3. **Tests pass without modification** — if the refactoring requires test changes, the scope has leaked beyond pure refactoring. Pick a smaller goal.
   - **Exception:** a test may mock at the wrong layer (Python-level `patch` instead of HTTP transport mock) and still try to verify real behavior underneath. In that case the test is valuable but brittle — you may fix the test to mock at the correct boundary as part of the refactoring, but only if the test truly exercises a real functional requirement. If the test only asserts call patterns (`mock_fn.assert_called_once_with(args)`) with no behavioral assertion, it adds no value — delete it.
   - **Do not keep redundant tests.** Two tests that cover the same code path with the same assertions are waste.
4. **Measurable improvement** — you can state before/after: "removed 5 dead event handlers", "consolidated 3 auth guards into one middleware", "replaced 4 magic status-code literals with named constants".
5. **The user can understand it from the PR title alone** — "Consolidate duplicate pagination helpers into shared module" is good. "Clean up server.py" is vague.

A goal will typically involve multiple individual refactorings (extract a shared function, update all callers, fix imports). That is fine — the constraint is that every change in the PR serves the same axis and addresses the same coherent goal.

Prioritisation:

1. **🔴 Must-fix items in hub modules** — hub modules affect the most code.
2. **🔴 Must-fix items in any module** — hard rule violations (blocking the event loop, empty except).
3. **🟡 Items that span multiple files** — cross-module duplication, single-source-of-truth violations. High-value because a single change fixes multiple violations.
4. **🟡 Items within a single file** — naming, types, long functions. Bounded, lower risk.
5. **🔵 Items** — cosmetic improvements. Only if nothing else is available.

### Present the Goal for Approval

Before executing anything, present the selected goal to the user:

> "Proposed goal: [one-line description]
> Files affected: [list]
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
| **Rename** symbol across files | **Rope** via inline Python | Write a `uv run python` script using `rope.refactor.rename` |
| **Extract** function/variable | **Rope** via inline Python | Write a `uv run python` script using `rope.refactor.extract` |
| **Move** symbol to another module | **Rope** via inline Python | Write a `uv run python` script |
| **Remove** dead code | Manual deletion | Find via `vulture` or grep analysis, then delete |
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

**Rope rename example:**
```python
from rope.base.project import Project
from rope.base.libutils import path_to_resource
from rope.refactor.rename import Rename
project = Project(".")
resource = path_to_resource(project, "houses/enricher.py")
refactoring = Rename(project, resource, offset)
changes = refactoring.get_changes("new_name")
project.do(changes)
project.close()
```

**Verification (mandatory):**
- Run `make test` — all tests pass
- Run `make lint` — no new errors
- Visually inspect the changed code with Read
- `git diff --stat` to confirm blast radius
- `git commit -am "refactor: <what changed and why>"`

**If neither tool can handle the change** (e.g., splitting a module), fall back to manual editing with extra checks:
- Read the full diff with `git diff`
- Run `make test`
- Run `make lint`
- If in doubt, don't make the change

## Step 6: Measure — Rebuild Graph and Compare

After the commit, rebuild and re-run key metrics:

```bash
uvx code-review-graph build
uvx code-review-graph postprocess
uvx code-review-graph status
uvx code-review-graph wiki
uvx code-review-graph detect-changes --base HEAD~1
```

Compare against the pre-change state from Step 1:
- Did the number of large modules decrease?
- Did cross-module duplication disappear?
- Did the risk score in `detect-changes` drop?
- Are there fewer private-symbol imports across module boundaries?

If a metric got worse, the refactoring may have increased coupling — `git reset HEAD~1 --hard` and reconsider. The pre-change `detect-changes` output from Step 1 serves as the baseline.

## Step 7: Create a Pull Request

Create a PR with a summary of what was changed and why. Reference the specific violations addressed. If the measure step showed improvements, include those numbers.

## Interaction With Other Rules

- **test-first.md** — Governs TDD process. Testing quality principles are in `docs/coding-standards.md`.
- **code-quality.md** — This rule. The review phases cover testing violations.
- **use-existing-tools.md** — Use `/dump-sheet` rather than writing inline Python for sheet data.
- **fail-fast.md** — Fail-fast principles are review criteria.
