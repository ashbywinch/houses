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

`classify_exception(exc) → ExceptionClassification(code, retryable)` is the
ONE source of truth for retryability — used by both `AttemptError` and the
DAG retry machinery.

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

### Dynamic dependency sets: compose a provider, never read `self` during construction

A node whose dep SET changes at runtime (destinations added/removed in Settings) passes a
zero-arg callable instead of a tuple:

```python
# The closure captures CONSTRUCTOR ARGUMENTS, never `self` — the base
# evaluates it lazily (staleness, refresh, provenance), never during __init__,
# so there is no construction-ordering hazard to misuse.
def __init__(self, node_id, *, selectors: dict[str, Node], persons_source: Node):
    self._selectors = selectors  # the LIVE dict, mutated in place by the owner
    super().__init__(node_id, dict, deps=lambda: (*selectors.values(), persons_source))
```

Rules that make this safe for every future node:

- The provider closes over its **arguments**, never over `self` — the base may consult it at
  any point in the node's life.
- Whoever owns the underlying dict **mutates it in place** (never reassigns) and **schedules
  the node explicitly** after structural changes — a provider's dynamic deps carry no change
  signals of their own.
- Never override `_get_active_deps()` to read subclass state assigned after
  `super().__init__()`: registration runs `_is_stale()` → `_get_active_deps()` inside the
  base constructor, and that ordering killed the live server on 2026-09-09.

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
| **Reads and writes are non-blocking** | A read (serialization/API) serves persisted state as-is and NEVER walks the graph or recomputes. Every recompute is scheduled by whatever makes it necessary — a dependency write, or a deploy invalidating persisted fingerprints (`PropertyNodes.schedule_code_stale_nodes()` at startup). The queue dedupes by node id and drains in the background; a first boot may take tens of minutes to settle, and that is expected. |

### Bumping node_id

When `compute()` changes such that old persisted results are semantically invalid, bump the node_id (`"{rid}/town_desc_v2"`). New node_id has no persisted data → `pending()` → recomputes. Old results orphan harmlessly. When old results are merely *wrong* (not meaningless), prefer `POST /api/admin/regenerate` — see `docs/development.md` → Fixing Bugs That Produced Wrong Persisted Data.

**When NOT to bump:** cosmetic refactors, adding logging, changing error messages, any change producing the same output for the same inputs.

## Wiring rules

Six ways to wire a calculation into the graph so that it stops working. Each
rule is a prohibition; the check beside it catches a regression.

| Never | What it causes | Check |
|---|---|---|
| Copy a dependency's value into a node | the node holds the old input; no change re-prices it | two-write test |
| Decide a value while building the pipeline | the decision outlives the data that justified it | the decision appears in provenance with its inputs |
| Hide a failure to keep a total tidy | a plausible number that silently omits a cost | `test_commute_failure_surfaces.py` |
| Explain a value outside its provenance | debugging by guesswork, and no evidence for the next reader | a failed value's provenance names the failure |
| Show implementation names to the user | the reader cannot act on the message | no node id, class name or Python identifier in a user-facing payload |
| Write the calculation twice, in code and in prose | a second, untested implementation that drifts | review finding |

### Never copy a dependency's value into a node

A node that copies a value while it is built (a `poi_info=poi` option, a
push-once source) holds that value for its lifetime: when the real input
changes, this node does not recompute, so the figure it reports and the
provenance it shows describe an input that no longer exists. Provenance is
versioned per result — the defect is that a new result is never computed.

```python
# ✗ the destination is copied in: moving it changes nothing
RouteOptions(poi_info=poi_snapshot)
# ✓ the node that owns the destination is the dependency
RouteOptions(poi=destination_node)
```

**Check:** a two-write test — change the source, assert the derived value
*and* its provenance both move (`docs/testing-standards.md` → A derived
value is tested by moving its input).

### Never decide a value while building the pipeline

"This destination is in the charge zone, so driving is not an option" is a
value. Decided while the pipeline is wired, it outlives the address that
justified it.

```python
# ✗ build-time decision: fixed for the life of the pipeline
if in_congestion_zone(poi.address):
    modes = modes_without_driving
# ✓ the gate is a node over live inputs, so it re-decides
drive = DriveNode(..., zone=congestion_zone_node)
```

The gate then appears in provenance with its inputs, which is how a reader
sees *why* driving was refused.

### Never hide a failure to keep a total tidy

Three forms, all forbidden:

- dropping a failed dependency from a node's dep set so its failure "cannot
  propagate";
- wrapping a failure into `succeeded(None)` so an aggregate keeps producing
  a number;
- building sweeps, retries or reconciliation to find and re-run what the
  narrowing hid.

`Attempt.impossible` is a permanent error — a 404/500, a dead service, a
crash. It propagates to the UI by itself (the framework does it), so the
user sees it where the value would be and somebody can fix it. Infeasibility
— "no walking route from here", "never drive into the charge zone" — is a
**succeeded** value carrying its reason: it flows as a value, and the totals
stay computable.

**The tell:** if you are building apparatus to compensate for a dependency
you removed, the removal is the bug.

**Check:** `tests/unit/nodes/test_commute_failure_surfaces.py`.

### Never explain a value outside its provenance

Reaching for a debug endpoint, a script or a log-only channel to answer "why
is this value empty, or wrong?" means the provenance is incomplete.

A value's provenance must fully explain it: the calculation, and for a
failure which input failed and why — the same detail the logs carry. Fix the
provenance, not the tooling.

**Check:** read the provenance of a failed value and assert the failure is
identifiable from it alone.

### Never show implementation names to the user

A node id (`{rid}/{person}/{destination}/bus_route`), a class name, a service
or endpoint name, a Python identifier — anywhere the user reads, including
inside provenance.

**Fix the label; do not delete the content.** Removing an input, a step or a
factor from provenance because a technical name appeared in it destroys the
evidence — provenance is the calculation. Give the node its `display_name`
and its algorithm a `description` in the domain's words
(`Node.display_name`, `Provenance.description`, `provenance_formula`); a node
id in provenance means that node never set one.

**Exception: an exception's own message is shown verbatim.** There is no way
to translate an arbitrary exception into a user-facing sentence, and
inventing one is worse than showing the real thing; its type and traceback
stay in `error_detail` and the logs.

**Map keys are not user-facing text:** `Provenance.sources` is keyed by node
id, and the UI renders each child's `label`, never the key — do not rename
or delete a key to satisfy this rule.

**Check:** a user-facing payload carries no node id, class name or Python
identifier; an exception message is the one permitted exception.

### Never write the calculation twice

A provenance description that restates `compute()` — the same thresholds,
the same branches, the same rule written again in prose — puts the logic in
two places: the prose drifts, and nothing tests it.

Provenance is composed from the nodes: each node names itself and declares
its inputs, and a description says **what** was done, never **how** it was
computed.

**The tell:** if describing a node to the reader means writing its
calculation a second time, the node holds more than one calculation. Split
it into one node per calculation.

**Check:** review — a provenance description encoding thresholds or branches
that also exist in `compute()` is a finding.

## Thread rules

The DAG pipeline runs on ONE background thread — the processor. The
uvicorn event loop never runs cascade work: it enqueues, serves reads
from memory, and fans out broadcaster pushes.

| Thread | Does | Never does |
|---|---|---|
| Event loop | requests, enqueues, in-memory reads, broadcaster fan-out | compute, persist, serialize DAG values |
| Processor (`dag-processor`) | drain the ONE queue in order: recompute → persist → emit | touch the UI |

Rules (every one is enforced — guard messages cite this section):

1. **Mutate DAG state only on the processor.** Request handlers mutate
   via `await run_on_processor(...)`; direct `push`/`refresh`/persistence
   off the processor raises (`assert_mutation_allowed`). Startup,
   scripts and tests never start a processor, so they are exempt by
   construction — no flags to juggle.
2. **Never mutate a value in place — replace it.** Nodes hold frozen
   dataclasses / Pydantic models and swap whole values (`self._value =
   ...`). That is what makes lock-free reads from the event loop safe:
   a reader walks a snapshot that cannot change underneath it.
3. **Persistence is a pipeline step.** `save_node_result` runs on the
   processor, in cascade order; `node_results` history order == cascade
   order. Nothing else writes the DB.
4. **Reads never wait and never flush.** Latest-state readers use
   `latest_attempt()`; history readers (`node_result_before`) use
   timestamp predicates that exclude unwritten rows by construction
   (what-if restore captures its boundary BEFORE the scenario push, so
   `created_at < started` can never match in-flight work).
5. **Freshness is push-delivered.** A read racing a cascade returns the
   previous snapshot; the broadcaster corrects it. Never poll, never
   re-read to "check for updates".
6. **Test data never enters the real DB.** Every property RID is 6-10
   digits; rows under any other RID are pollution. If any appear, delete
   them immediately (back the rows up first) and fix the writer — the
   startup loader refuses to boot over them.

7. **The front end never blocks on the queue. No exceptions.** Edit
   endpoints enqueue the mutation and return immediately — they never
   flush, never wait for the cascade or any part of it. The UI updates
   from the websocket: `property_updated` per property, one coalesced
   `settings_updated` per settings burst. A read that races the drain
   returns the previous snapshot (rule 5); the broadcaster corrects it.
   Anything requiring external calls (routing, geocoding, scraping)
   drains only in the background — an inline flush there would hang the
   request for the whole backlog.

Shutdown sentinels the processor: remaining work drains in order, then
the loop stops (bounded join — `systemctl stop` must not hang).

### Tests

The pipeline is real in tests, driven synchronously: no processor thread
(`start_processor` no-ops under `testing`; a fixture asserts none was
started). `flush_all()` drains the queue once after the operation under
test — compute and persistence land together. `drain_recompute()` is for
read-helpers that need pending computation only. A no-op `flush_all()`
raises: it means you are flushing to make a read see writes, which is
never needed — reads are snapshot-safe by design.


## Debugging

### Tracing failures through provenance

Every node's `to_json()` includes a `provenance` dict. On failure, read the failed node's `node_results`, then its deps' — repeat to the root cause.

**Investigating a suspicious value: never delete rows, clear caches, or
restart to make a question go away** — that destroys the evidence. Read
the provenance chain instead. The one exception is **test-data rows in
the real DB** (any property RID that is not 6-10 digits, or one-shot
debug node ids): they must be removed IMMEDIATELY on discovery — back up
the rows, delete them, fix whatever wrote them. The startup loader
refuses to boot over them.


### DB isolation for tests

Tests replace the global DB connection with in-memory SQLite. Settings sources live in `Services` (not module level), so they read the in-memory DB automatically.

## Datetime rules

- Store/process UTC (`datetime.now(UTC)`, never `datetime.now()`).
- After `datetime.fromisoformat()`, check `dt.tzinfo is None` and replace with UTC.
- Display in the user's local timezone at the presentation boundary.
- External APIs: document the source's timezone and convert explicitly.
