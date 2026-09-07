# Plan: the DAG pipeline on a background processor

Supersedes the "background save thread" design (preserved at
`992ad1f` on `fix/dag-save-queue-reconciliation`). The save-thread design
stopped halfway: it moved only persistence off the event loop and left the
cascade processor — the actual UI-locking workload — running as an asyncio
task on the loop. This design evicts the whole pipeline.

## User requirements (non-negotiable)

1. The event loop MUST NEVER run cascade work — no recomputes, no
   persistence, no serialization. Not during a cascade, not during
   first-boot settling, not ever. It handles requests, enqueues, serves
   reads from memory, and fans out broadcaster pushes.
2. One queue. Everything the DAG does runs in scheduled order on the
   processor: invalidate → recompute → persist → emit. Persistence is a
   pipeline step, not a competing workload.
3. Concurrency correct BY DESIGN, not test-validated. The proof must fit
   in a paragraph (it does — see "Why this is correct").
4. Cascade latency may rise if the UI stays responsive (accepted:
   compute → persist → compute serializes; milliseconds per node).
5. Tests drive the same pipeline synchronously and deterministically.

## Architecture

```
event loop (uvicorn)                     processor thread
────────────────────                     ──────────────────────────────
requests ──┐                             own private asyncio loop
enqueue ───┼── ONE FIFO queue ──────────▶ drain in order:
reads ─────┘                             1. recompute item: await
broadcaster ◀── threadsafe handoff ──────    node.compute() (httpx/TfL/LLM)
(fan-out)                                2. persist step: BLOCKING sqlite3
                                             write — harmless here
                                         3. emit: node changed →
                                             run_coroutine_threadsafe →
                                             broadcaster task on main loop
shutdown: sentinel → drain → join
```

- **The event loop never computes and never persists.** Request handlers
  enqueue work and serve in-memory state. This kills the original
  complaint — cascades locking the UI — at the root: the whole pipeline,
  not just its writes, leaves the loop.
- **One queue, one consumer.** No save queue, no save thread, no
  aiosqlite. Persistence is a blocking call on the processor, where
  blocking is the *point*: it keeps every effect in one ordered pipeline.
  The queue count follows from the executor count: one processor, one
  queue.
- **Order is total.** compute(N) → persist(N) → compute(N+1): the
  append-only `node_results` history lands in exact cascade order —
  what `node_result_before()` and `dep_timestamps` freshness assume.
  Strictly stronger than the two-queue design it replaces.

### Why this is correct (the whole concurrency proof)

A queue is the waiting room of its consumer. There are two executors:

- The **event loop** runs request handlers only.
- The **processor thread** runs the pipeline. Its async computes need a
  loop, so the thread owns a private one; its persistence is blocking,
  which only a non-loop thread may do.

Memory safety between them rests on two existing house rules:

1. **Single writer.** All DAG mutation happens on the processor thread.
2. **Immutable values, swapped by reference.** Nodes never mutate values
   in place — `push`/compute *replace* whole frozen dataclasses /
   Pydantic models (`self._value = ...` is one GIL-atomic assignment). A
   request handler serializing a property walks a snapshot that cannot
   change underneath it; worst case it is one step stale.

Freshness is push-delivered, not poll-hoped: the broadcaster tells the
frontend when things change (this already exists and does not change). A
GET racing a cascade serves the current snapshot; the push corrects it.
Stale reads are a non-event because readers are guaranteed to be notified.

### Reads never wait, never flush

- **Latest-state readers** (serialization, cards, detail pages) read
  in-memory attempts — always the current snapshot.
- **History readers** (`node_result_before`) are safe by construction:
  timestamp predicates exclude unwritten rows. Worked example — what-if
  restore: apply captures the boundary `started = now()` BEFORE the
  scenario push, every scenario write is stamped `created_at > started`,
  and the restore query is `created_at < started`. In-flight work can
  never match; the wanted rows are older committed history.
- **No request path ever flushes or waits on the queue.** The only
  drain outside the processor is shutdown.

### Cross-thread details (work, not obstacles)

- The processor thread owns a private `asyncio` loop for the async
  compute functions (a thread can own a loop; it need not be uvicorn's).
- `after_refresh` broadcaster pushes cross threads via
  `run_coroutine_threadsafe(...)` onto the main loop's broadcaster task.
- Node-state visibility: request handlers already interleave with
  cascades today (at await points); moving the cascade to a thread
  changes granularity, not kind. The immutable-swap rule above is what
  makes it safe — keep it (it is already in coding-standards.md).

## Test strategy

The pipeline is REAL in tests — driven synchronously at the choke points
the suite already uses.

- The processor never runs as a thread in tests (`testing` gates thread
  start; the drain runs on the test thread — which is also why
  `:memory:` keeps working).
- **`flush_all()` drains the one queue to exhaustion** — recompute and
  the persistence steps it produces — called ONCE after the operation
  under test, before assertions.
- **`drain_recompute()`** exists for read-helpers that need pending
  *computation* to land before a serialized read (e.g. `_pimlico_commute`)
  and must not touch persistence.
- **Fixture setup clears the queue** (isolation); teardown drops quietly
  via the internal path.
- **No-op flush guard**: a `flush_all()` that drains nothing, where
  nothing was enqueued since the last flush, raises with a pointer to
  `docs/dag-library.md` → 'Persistence: reads, writes, and the queue'.
  The wrong instincts (flush-per-write, flush-so-my-read-can-see-writes,
  double flush) all fail loudly; legit multi-flush tests never trip.
- Tests that were written against the old semantics (the what-if suite,
  built without the architecture in mind, then duct-taped with inline
  flushes) are fixed as wrong: operation → one `flush_all()` → assert.

## What changes

| Area | Change |
|---|---|
| `dag/scheduler.py` | Processor becomes a thread with a private loop draining ONE FIFO queue (recompute + persist steps in order); sentinel + bounded join; cross-thread broadcaster handoff |
| `dag/persistence.py` | `save_node_result` stays the write path, called by the processor in order; writer-thread guard becomes processor-thread guard; **delete** the separate save queue/thread from the superseded design |
| `dag/node.py` | `_persist()` enqueues a persist step (or the processor persists inline on compute completion — implementation detail, decided at the code) |
| `houses/server.py` (lifespan) | Start processor thread at startup; sentinel + bounded join + log-and-proceed at shutdown |
| `houses/web/broadcaster.py` | Accept threadsafe submission from the processor |
| `tests/unit/conftest.py` | `flush_all()` drains the one queue; no-op guard; `drain_recompute()` split (done) |
| `tests/unit/isolation_fixtures.py` | Queue clear at setup (done) |
| `docs/dag-library.md` | Rewrite 'Persistence: reads, writes, and the save queue' section for the processor design |
| Cleanup (done) | Route-handler flush deleted; sprinkled per-test flushes deleted; what-if tests fixed as wrong; worker-reliability tests relocated |

## What does NOT change

- The broadcaster's UI contract: pushes on change — the frontend never
  knows a thread exists.
- Read endpoints and their semantics (serve the current snapshot).
- `UserInputNode.push` semantics; `_persist()` call sites' shape.
- The immutable-value rule — it is now load-bearing for memory safety.

## Accepted trades

- **Cascade latency**: compute → persist → compute serializes; a cascade
  finishes later by (write+handoff) per node. The UI is responsive
  throughout — that is the trade, deliberately chosen.
- **Crash window**: work in the queue is lost on a crash (never issued to
  SQLite). Derived nodes self-heal via staleness/recompute; user inputs
  can revert; window is milliseconds outside mid-cascade. Accepted —
  single-family app, DB-file backups exist
  (`docs/deployment-oracle-free-tier.md`).
- **GIL granularity**: request handlers can interleave with processor
  steps at bytecode boundaries rather than await points. Immutable-swap
  rule makes this safe; no per-read locking, ever.

## Reasoning summary

One queue, one processor, one writer thread's worth of discipline: the
event loop serves, the processor works, values are immutable, freshness
is pushed. Each queue is FIFO with a single consumer and the second stage
only receives what the first finished — a chain, not a weave. The whole
concurrency proof: request handlers read snapshots that cannot change
under them, the processor is the only mutator, and the broadcaster tells
everyone who cares. Nothing else is load-bearing.
