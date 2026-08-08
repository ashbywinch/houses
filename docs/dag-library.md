# DAG Library — `dag/`

**For:** developers adding/maintaining DAG nodes in `houses/nodes/`.

The `dag/` package is a reactive directed acyclic graph: when a leaf changes, every downstream node recomputes and persists. API surface (node classes, `Attempt`, expressions, scheduler) lives in the code — `dag/node.py`, `dag/attempt.py`, `dag/expression.py`, `dag/derived_node.py`, `dag/scheduler.py`. Use the code-review graph (`file_summary` / `children_of`) or read those files for signatures.

This doc records the **rules and conventions** that aren't discoverable from the code.

## The three-state result: `Attempt[T]`

`Attempt` is a discriminated union: `succeeded(value)` / `pending()` / `impossible("reason")`. See `dag/attempt.py`.

**`compute()` MUST return an `Attempt`.** The framework short-circuits: dep impossible → propagate upstream (never call compute); dep pending → defer.

### AttemptError — structured errors

`Attempt.error_info` is an `AttemptError{code, message, user_message, retryable, source, causes, exc, traceback}` — see `dag/attempt.py`.

**Never parse error strings. Inspect the exception:**

```python
# ✗ string parsing
if "429" in attempt.error:
    retry = True
# ✓ structured
retry = attempt.error_info.retryable
status = attempt.error_info.exc.status   # actual HttpError
```

**Field split (UI vs internal):**

| Field | Content | Render in UI? |
|---|---|---|
| `error` | `display_message` — friendly leaf text ("Works estimate required for: Ashby") | ✅ |
| `error_detail.message` | full internal chain, node ids, `dep failed` markers | ❌ debugging only |
| `error_detail.traceback` | captured traceback | ❌ debugging only |

`display_message` resolution: explicit `user_message` → deepest cause's message → `message`. Dep-failure chains surface the leaf reason, never ids.

`classify_exception(exc) → (code, retryable)` is the ONE source of truth for retryability — used by both `AttemptError` and the DAG retry machinery.

### Error propagation contract

| Service type | Failure channel | Transient errors |
|---|---|---|
| Calls APIs | return `Attempt[...]` | re-raise (`httpx.HTTPStatusError`, `RequestError`, timeout) → DAG retries |
| Pure computation | throw | n/a — framework catches, records on Attempt |

**Never lose the reason.** ✗ `return None` / `return ""` on failure (caller can't distinguish "no data" from "API down"); ✓ `Attempt.impossible(reason)`.

```python
# ✗ swallows the reason
async def lookup(pc: str) -> str | None:
    try: ...
    except Exception:
        return None
# ✓ propagates it
async def lookup(pc: str) -> Attempt[str]:
    try: ...
    except httpx.HTTPStatusError:
        raise                       # transient → DAG retries
    except Exception as e:
        return Attempt.impossible(f"lookup failed: {e}")
```

Retry decision = `AttemptError.retryable`, regardless of raise-vs-return style.

### Nodes: propagate, never re-literalize

**Never** replace a failed dep/service reason with a fresh string:

```python
# ✗ invents a literal, hides the real reason
if not result.succeeded:
    return Attempt.impossible("no data")
# ✓ propagate
if not result.succeeded:
    return Attempt.impossible(result.error or "no data")
```

`Node._impossible(deps)` and the dep-failure path build `code=dep_failed` with a `causes` chain — traverse structurally, never by string match.

## Expression System

Nodes can declare `expression` (an `Expression` tree in `dag/expression.py`) instead of an imperative `compute()`; the base class evaluates and auto-generates provenance. Node objects work directly in expressions via `__add__`/`__sub__`/`__mul__`/`__truediv__`/`__neg__`.

```python
self._price_node + self._stamp_duty_node - self._equity_node
# Creates: Sub(Add(Ref(price), Ref(stamp_duty)), Ref(equity))
```

Expression-based nodes **do not override `build_provenance()`** — the base default walks active deps and calls `expression.to_formula()`.

### Stable dependencies by default

`_get_active_deps()` should return the same deps in most cases. **Conditional deps are for shortcutting — skipping computation entirely.** If you want all alternatives computed then compared, keep deps stable. For trivial local computation (reading a cached value, geocoding a coordinate), stable deps avoid unnecessary conditional complexity:

```python
# Correct: stable deps, early-return in compute when dep isn't needed
def __init__(self, ..., transit_result, best_location):
    super().__init__(node_id, Commute, (transit_result, best_location))

def compute(self, transit, location):
    if transit.value_or_none().daily_cost.amount > 0:
        return transit  # early return, NR lookup not needed
    return await self._enrich_rail_fare(commute, location)
```

## Settings Nodes

Every financial setting has its own `UserInputNode`, created by `Services.__post_init__` from `SETTING_DEFAULTS` in `houses/nodes/settings_node.py`. Consumer nodes reference individual setting nodes directly, never a blob. `SettingsNode` aggregate exists only for API backward compat (`svc.settings_view`) — consumer code never uses it.

## Design Rules

| Rule | Detail |
|---|---|
| **One concept per node** | `compute()` does one thing; split otherwise — signal chain tracks real deps, downstream depends on just what it needs |
| **Stable dependencies** | `_get_active_deps()` always returns the same tuple; if a dep is sometimes unnecessary, keep it and early-return in `compute()` |
| **No side effects in compute** | Never push into other nodes — use a dependency chain |
| **Typed values, not dicts** | Frozen dataclasses / Pydantic models so the value type is self-documenting and the TypeAdapter round-trips safely |
| **Service results wrapped in Attempt** | `School | None` → `Attempt.succeeded(school)` or `Attempt.impossible("not found")` |

### Bumping node_id

When `compute()` changes such that old persisted results are semantically invalid, bump the node_id (`"{rid}/town_desc_v2"`). New node_id has no persisted data → `pending()` → recomputes. Old results orphan harmlessly. When old results are merely *wrong* (not meaningless), prefer `POST /api/admin/regenerate` — see `docs/development.md` → Fixing Bugs That Produced Wrong Persisted Data.

**When NOT to bump:** cosmetic refactors, adding logging, changing error messages, any change producing the same output for the same inputs.

## Debugging

### Tracing failures through provenance

Every node's `to_json()` includes a `provenance` dict. On failure, read the failed node's `node_results`, then its deps' — repeat to the root cause.

**Never delete DB rows, clear caches, or restart the server to investigate** — that destroys the evidence. Read the provenance chain instead.

### DB isolation for tests

Tests replace the global DB connection with in-memory SQLite. Settings sources live in `Services` (not module level), so they read the in-memory DB automatically.

## Datetime rules

- Store/process UTC (`datetime.now(UTC)`, never `datetime.now()`).
- After `datetime.fromisoformat()`, check `dt.tzinfo is None` and replace with UTC.
- Display in the user's local timezone at the presentation boundary.
- External APIs: document the source's timezone and convert explicitly.
