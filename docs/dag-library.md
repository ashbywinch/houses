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

### Leaf facts can fail — record the failure on the node

`UserInputNode` holds a value, nothing, or a **recorded failure**. A source
that saw data but could not read it MUST record that on the owning node —
storing it anywhere else makes it invisible: `to_json_value` emits only
attempt state, so nobody downstream ever sees it.

**To record a failure, call `node.fail(message, *, error_info=…)`.**
`fail()` persists an `impossible` row; `attempt()` and every `to_json`/
`to_json_value` report `impossible` + the reason; derived nodes see a failed
dependency and propagate `dep_failed`. `push()` clears a failure — a real
value always wins.

```python
# ✗ do NOT — a failure stored off-node is read by nobody
source_result = SomeSource(result=None, errors={"price": "..."})
# ✓ do — the owning node's attempt is the DAG, wire, and UI
price_node.fail(
    "price value could not be parsed",
    error_info=AttemptError(code="parse_error", source="scraper", ...),
)
```

**Do:**

- Call `fail(...)` when the source saw data it could not interpret.
- Leave a node unset (no push) when the source genuinely has no value for it.

**Never:**

- Store error info outside the node: no `errors` dict on a source result, no
  error field on an enriched record, no extra key on a wire dict.
- Call `fail(...)` for a legitimate absence — a source with no value for a
  field leaves the node unset; no value is a valid state, not an error.
- Leave a node unset when the source SAW data but could not read it — the
  result reads as absence and hides the failure.
- Push placeholder values as data (`0`, `""`, a default amount) — whatever
  is pushed IS the value; downstream cannot tell a placeholder from a real
  fact.

## Expression System

Nodes can declare `expression` (an `Expression` tree in `dag/expression.py`) instead of an imperative `compute()`; the base class evaluates and auto-generates provenance. Node objects work directly in expressions via `__add__`/`__sub__`/`__mul__`/`__truediv__`/`__neg__`.

```python
self._price_node + self._stamp_duty_node - self._equity_node
# Creates: Sub(Add(Ref(price), Ref(stamp_duty)), Ref(equity))
```

Expression-based nodes **do not override `build_provenance()`** — the base default walks active deps and calls `expression.to_formula()`.

### Provenance serves the frozen row, never a re-read

Each derived row stores its provenance tree inside the same
zlib-compressed `result_json` blob (`dag/persistence.py:compress_result`),
frozen at persist time:

```python
# result_json (decompressed) — what a row carries
{
    "provenance": {...},  # full tree, frozen at persist time
}
```

The row carries ONLY that tree — no parallel flat map. `refresh()`
(`dag/derived_node.py`) binds the exact attempts the value was
calculated from — `dep_attempts = [await dep.attempt() for dep in
active_deps]` — and persists the tree built by
`build_provenance(dep_attempts, active_deps)` in the same `_persist`
call. Dep subtrees render from those bound attempts
(`_attempt_provenance`), never from the dep nodes. Formulas that need
dep values override `provenance_formula_for(dep_attempts, active_deps)`
and read the bound attempts. Nodes whose formula ignores deps keep the
default, which delegates to `provenance_formula`.

**The record is the value AND the inputs that produced it.** A dep
subtree is the dep's own recorded derivation (what IT calculated from)
patched with the attempt this node actually bound; a dep with no
recorded row renders a leaf from the bound attempt — never a read of
the dep's current state, which may hold a value the compute never saw.
Formulas read the bound attempts too.

Refreshing to an identical value from the SAME inputs keeps the
original row and clocks (downstream stays parked). An identical value
from DIFFERENT inputs re-records the row — it states the inputs this
evaluation used — and emits **no** change: the value is unchanged, so
dependents have nothing to recalculate (and a planner must not re-plan
because a stamp moved).

Serve (`build_provenance()` with no args) returns the frozen row
verbatim — `Provenance.from_dict(row["provenance"])`. No join, no
recursion into dep rows, no live build, no `latest_attempt()`. A node
with no stored row yet (constructed but never flushed) renders its
own live status with an empty source set — a test-setup gap, never
the contract.

Wrong fixes — each reintroduces a re-read instead of serving the row:

```python
# ✗ render a formula from live deps (they may have moved on)
mpg = self._mpg_node.latest_attempt().value_or_none()
# ✓ read the attempt the compute bound
mpg = bound[active_deps.index(self._mpg_node)]
# ✗ render a dep subtree from its live state at persist time
sources[dep._id] = await dep.live_provenance()
# ✓ its recorded derivation, patched with the bound attempt
sources[dep._id] = recorded_subtree(dep, bound_attempt)
# ✓ serve returns the frozen row verbatim
return Provenance.from_dict(row["provenance"])
```

### A node states only what it depends on

A node's provenance is the record of its own calculation, so it can
report only what its deps gave it. If `compute` never saw a fact, the
value and the tree must not claim it — not as a default, not as a
carried-over copy.

The route planners are the worked example: a route from A to B depends
on the origin and the destination ADDRESS. Frequency does not change
the route, so the planner does not depend on it, so its value carries
no frequency (`Commute.destination` stays `None`) and its provenance
states none. The node that DOES depend on the place owns that claim —
the selector stamps the place onto the winner it picks, and everything
downstream carries it.

```python
# ✗ the planner invents a frequency it never read (dataclass default)
destination=PlaceOfInterest(label="", address=dest_str)   # trips_per_week=1
# ✓ the planner reports the journey it planned; no destination claim
destination=None
# ✓ the node with the place dep makes the claim
val = replace(val, destination=inputs.poi.value_or_none())
```

### Narrow deps to what `compute` reads

`_get_active_deps()` answers "what does THIS evaluation depend on" —
staleness, refresh, and provenance all flow through it. The default
is the full dep tuple; override it to exclude deps whose changes
cannot change the value. A refresh the node doesn't need is pure
waste (and on a trips-only edit, a needless API call) — narrowing
the dep skips the refresh entirely, not just the computation.

This is different from early-return in `compute` (which still
refreshes, persists, and rebuilds provenance) and from `IfThenElse`
branch selection (which picks one live branch). Narrowing says the
excluded dep is irrelevant to this evaluation at all.

```python
# stable deps + early return: still refreshes on every dep change
def compute(self, transit, location):
    if transit.value_or_none().daily_cost.amount > 0:
        return transit  # NR lookup not needed — but the refresh already happened
# narrowed deps: an irrelevant dep change never schedules the node
def _get_active_deps(self):
    deps = [self.transit_node]
    if self._fare_matters():
        deps.append(self.fare_node)
    return tuple(deps)
```

Precedents in the tree: `MergeRailFareNode` drops the fare dep for
drive/walk selections; `ParkAndRideAugmentNode` drops a pending
postcode; `IfThenElseNode` drops the unchosen branch. In each case
the excluded dep's changes correctly do nothing.

#### A dep object holding both used and unused fields is NOT both-or-neither

Example: a route planner depending on the whole POI when `compute`
reads only its address. Trips-only edits then re-plan routes. The
wrong fixes all work around the refresh instead of narrowing the
dep — each duplicates DAG state outside the DAG:

```python
# ✗ patch the persisted provenance strings after the fact
node.value = _FREQ_RE.sub(fresh, node.value)
# ✗ schedule nodes by hand instead of letting signals do it
for nid, node in list(get_scheduler().registered_nodes().items()):
    if nid.endswith(("/walk", "/drive")):
        get_scheduler().schedule(node)
# ✗ stash planning inputs in node memory
self._planned_origin = loc
# ✗ widen the value to carry planning inputs
replace(val, destination=poi, origin=_origin_key(loc))
```

Fix the dep instead. In this order:

1. **Depend on the node that already produces the value.** The tree
   already has fine-grained projections of the fat aggregate —
   `PersonMaxWalkNode` and `PersonPetrolMpgNode` (one person's value
   out of the whole persons list), `DestinationPlaceNode` (one
   person/POI), `best_location`. Depending on one of those IS the
   narrowing; nothing new to build. If `compute` reads a field and a
   node already yields exactly that field, this is the answer.
2. **Project, then depend on the projection** — only when no such
   node exists. A pure `DestinationAddressNode(place) -> str`; the
   planner deps `(best_location, address_node)`. The full-POI stamp
   flows through the nodes that render it (selector → merge → fuel →
   breakdown), which keep the full dep. Trips-only pushes stop
   marking the planner stale at all.
3. **Delete an unread dep** — `compute` never reads it: remove it
   from the tuple. No projection needed.

If none fits — `compute` genuinely reads the whole object — the
refresh is real and any API call inside needs its own reuse guard at
the call site, not a scheduling workaround.

### Dynamic dependency sets: deps are nodes, rewired with `set_deps`

A node whose dep SET changes at runtime (destinations added/removed in
Settings) still takes NODES as its deps — never a lambda, never a dict,
never a provider closure. When the set changes, the owner rewires:

```python
def __init__(self, node_id, *, selectors: tuple[Node, ...], persons_source: Node):
    super().__init__(node_id, dict, (*selectors, persons_source))

def _on_persons_changed(self) -> None:
    build_commute_pipeline(self, keys=...)          # materialize new pipelines
    self.commute_breakdown.set_deps(                # then rewire the deps
        (*self.commute_selectors.values(), self._svc.persons_source)
    )
```

Rules that make this safe for every future node:

- **Deps are nodes.** A callable passed as `deps` raises `TypeError`. A
  closure's deps carry no change signals of their own, so a node built
  on one has no wiring — nothing signals it when its inputs change.
- **The owner calls `set_deps`** when the set changes: it disconnects
  every existing dep slot, swaps the deps, and connects the new ones, so
  each dep write signals the node through its own dep slot.
- Never schedule around a dep signal. If a node needs recomputing on a
  data change, that data must BE one of its deps — fix the dep list, not
  the scheduling. A manual `get_scheduler().schedule(node)` beside a dep
  that already signals does the scheduler's job for it and hides the
  wiring bug. Exception: the startup sweep. The in-memory queue is gone
  after a crash or deploy — no signal will ever fire for work queued
  before it died, and a deploy's code-stale fingerprints mismatch
  nothing any dep could observe. `dag/regenerate.py:
  schedule_code_stale_nodes` re-queues exactly that lost work and stays.
  Rule of thumb: a data change must arrive through a dep; a dead queue
  cannot signal, so the sweep recreates it.

```python
# ✗ the breakdown needs selector updates but holds no selector dep —
#   scheduling past the missing edge instead of adding it
get_scheduler().schedule(self.commute_breakdown)
# ✓ the missing edge, added: each selector write now signals the
#   breakdown through its own dep slot — nothing to schedule by hand
self.commute_breakdown.set_deps(
    (*self.commute_selectors.values(), self._svc.persons_source)
)
# ✓ the sweep re-queues work the dead queue lost (crash) or work no dep
#   can observe (code-stale fingerprints after a deploy) — the one
#   scheduling call no signal could replace
schedule_code_stale_nodes(n for n in vars(self).values() if isinstance(n, Node))
```
- Never override `_get_active_deps()` to read subclass state assigned after
  `super().__init__()`: registration runs `_is_stale()` → `_get_active_deps()` inside the
  base constructor, before the subclass fields exist — the override reads
  uninitialised state.

## Settings Nodes

Every financial setting has its own `UserInputNode`, created by `Services.__post_init__` from `SETTING_DEFAULTS` in `houses/nodes/settings_node.py`. Consumer nodes reference individual setting nodes directly, never a blob. `SettingsNode` aggregate exists only for API backward compat (`svc.settings_view`) — consumer code never uses it.

## Design Rules

| Rule | Detail |
|---|---|
| **One concept per node** | `compute()` does one thing; split otherwise — signal chain tracks real deps, downstream depends on just what it needs |
| **Narrow dependencies** | `_get_active_deps()` excludes deps whose changes cannot change the value (→ Narrow deps section); early-return in `compute()` still refreshes — narrowing skips the refresh |
| **No side effects in compute** | Never push into other nodes — use a dependency chain |
| **Typed values, not dicts** | Frozen dataclasses / Pydantic models so the value type is self-documenting and the TypeAdapter round-trips safely |
| **Service results wrapped in Attempt** | `School | None` → `Attempt.succeeded(school)` or `Attempt.impossible("not found")` |
| **Reads and writes are non-blocking** | A read (serialization/API) serves persisted state as-is and NEVER walks the graph or recomputes. Every recompute is scheduled by whatever makes it necessary — a dependency write, or a deploy invalidating persisted fingerprints (`PropertyNodes.schedule_code_stale_nodes()` at startup). The queue dedupes by node id and drains in the background; a first boot may take tens of minutes to settle, and that is expected. |

### Bumping node_id

When `compute()` changes such that old persisted results are semantically invalid, bump the node_id (`"{rid}/town_desc_v2"`). New node_id has no persisted data → `pending()` → recomputes. Old results orphan harmlessly. When old results are merely *wrong* (not meaningless), prefer `POST /api/admin/regenerate` — see `docs/development.md` → Fixing Bugs That Produced Wrong Persisted Data.

**When NOT to bump:** cosmetic refactors, adding logging, changing error messages, any change producing the same output for the same inputs.

## Wiring rules

Ten ways to wire a calculation into the graph so that it stops working. Each
rule is a prohibition; the check beside it catches a regression.

| Never | What it causes | Check |
|---|---|---|
| Copy a dependency's value into a node | the node holds the old input; no change re-prices it | two-write test |
| Decide a value while building the pipeline | the decision outlives the data that justified it | the decision appears in provenance with its inputs |
| Hide a failure to keep a total tidy | a plausible number that silently omits a cost | `test_commute_failure_surfaces.py` |
| Explain a value outside its provenance | debugging by guesswork, and no evidence for the next reader | a failed value's provenance names the failure |
| Show implementation names to the user | the reader cannot act on the message | no node id, class name or Python identifier in a user-facing payload |
| Write the calculation twice, in code and in prose | a second, untested implementation that drifts | review finding |
| Store derived state outside the value | restarts lose it, persistence cannot see it, a second source of truth | the value carries everything `compute` needs beyond its dep attempts |
| Depend on more than `compute` reads | trips-only edits re-plan routes; the workarounds below duplicate DAG state outside the DAG | narrow the dep; a refresh the node doesn't need is the tell |
| Read a dep's current attempt instead of the bound one | the frozen tree carries inputs the value was not calculated from | `compute` and formulas receive dep attempts as arguments; no `latest_attempt()` in node code |
| Schedule a node by hand | hides the missing dep edge, and the signal path can never reach the node | no `get_scheduler().schedule(...)` in node owners (the startup sweep is the sole exception) |

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

**The tell:** if you are writing a sweep, retry loop, or reconciliation pass to find and re-run nodes the graph should have scheduled, the missing dep (or the wrongly narrowed one) is the bug — fix the wiring, not the scheduler.

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

### Never store derived state outside the value

`compute` reads its dep attempts and returns a value; anything else it
needs (which origin it planned from, which revision it saw) lives ON
the value, never in node-instance memory (`self._last_*`,
`self._planned_*`, module caches). Instance memory is lost on
restart, invisible to persistence, and unreadable to provenance —
a second source of truth beside the DAG.

```python
# ✗ the origin lives beside the DAG: restarts re-plan, tests cannot see it
self._planned_origin = loc
# ✓ the origin rides the value: attempts, persistence and provenance carry it
replace(val, destination=poi, origin=_origin_key(loc))
```

**Check:** restart the process mid-scenario — the second run must not
re-plan. A field set in `compute` and read in the next `compute` is
the tell.

A planner using only the destination address but depending on the
whole POI (label + trips/weeks) re-plans on every trips-only edit.
The wrong fixes work around the refresh instead of narrowing the
dep: patching persisted provenance strings with a regex, scheduling
nodes by hand over the registry, stashing planning inputs in
`self._*` memory, widening the value to carry planning inputs —
each duplicates DAG state outside the DAG.

```python
# ✗ over-broad dep: trips-only edits re-plan the route
super().__init__(node_id, Commute, (options.best_location, options.poi))
# ✓ the address projection is the dep; the stamp flows downstream
super().__init__(node_id, Commute, (options.best_location, address_node))
```

Project first (`DestinationAddressNode(place) -> str`), depend on the
projection; the full-POI stamp flows through the nodes that render it
(selector → merge → fuel → breakdown). An unread dep is the same bug
with no workaround to tempt — delete it (`TownDescNode.best_location`,
`NearestSchoolNode.best_address`).

**Check:** a refresh the node doesn't need is the tell — a trips-only
push must not schedule the planner at all.

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
started). **Threading is never tested.** It is correct by construction:
`submit_to_processor` has no branch that needs a thread — with no
processor loop (tests, startup, scripts) it applies the work inline, and
on a live processor it is a `run_coroutine_threadsafe` handover counted
until its callback lands. `stop_processor` drains the queue AND that
count before joining. A test that starts a real processor thread asserts
scheduling, not a contract — and it brings a second thread to the
isolation fixture's one-thread connection, which is sqlite misuse by
definition. Such a suite is a flake generator, not a test suite. Pin
what is deterministic instead: value replacement in the caller's turn,
persistence through the seam, drain order. `flush_all()` drains the
queue once after the operation under test — compute and persistence land
together. `drain_recompute()` is for read-helpers that need pending
computation only. A no-op `flush_all()` raises: it means you are flushing
to make a read see writes, which is never needed — reads are
snapshot-safe by design.


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
