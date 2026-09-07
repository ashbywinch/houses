# Plan: DAG persistence on a background save thread

Replaces `/tmp/whatif-persistence-thread-plan.md` (superseded — its
correctness argument leaned on a false premise; see history in the session
of 2026-09-07).

## User requirements (non-negotiable)

1. The event loop MUST NEVER be blocked by persistence I/O — not during a
   cascade, not during rollout first-boot, not ever.
2. Tests MAY use an in-memory database, drained on the test thread — this is
   fine because tests are deterministic.
3. Concurrency must be correct BY DESIGN, not because tests validate it.

## Architecture invariant

**Writes: only the writer thread touches the DB. Everything else enqueues.**
**Reads: any thread, any time, never wait.**

This is not new architecture — single-writer was always the design. The
change moves the writer off the event loop.

### Single writer, enforced

- The DB write path is module-private: `_save_node_result()`. The public
  surface is `enqueue_save()` only.
- Runtime guard (fail-fast):

  ```python
  def _save_node_result(...):
      if not testing and threading.current_thread() is not _save_thread:
          raise RuntimeError("single-writer violation; enqueue_save() instead")
  ```

  A future route author who writes directly gets a loud crash in dev, not a
  silent second writer in prod.

### Reads never wait

- WAL is already on (`PRAGMA journal_mode=WAL`, per-thread connections) —
  readers see committed state as-is, at most microseconds stale, and never
  contend with the writer.
- Two read kinds, neither needs the queue:
  1. **Latest-state readers** (serialization, cards, detail pages) read
     in-memory `latest_attempt()` — always current.
  2. **History readers** (`node_result_before`) are safe by construction:
     their timestamp predicates exclude in-flight writes. Worked example —
     what-if restore: apply captures the boundary `started = now()` BEFORE
     the scenario push (`api_router.py` — marker line precedes persons
     push), every scenario write is stamped `created_at > started`, and the
     restore query is `created_at < started`. In-flight rows can never
     match; the wanted rows are older committed history. **No read ever
     flushes. The only legitimate flush caller is shutdown.**
- **Stale reads are a non-event** because freshness is push-delivered, not
  poll-hoped: the broadcaster notifies readers when the cascade settles and
  hands them the new values. Ordering invariant behind this: the broadcast
  originates from the same in-memory cascade that enqueued the writes, and
  the queue drains FIFO behind it — push consumers apply the pushed payload
  (in-memory, current); only full-reload readers touch the DB, at human
  timescales, long after the drain.

### Mechanism
- `_persist()` calls `enqueue_save()` → `_ensure_save_thread()` (fallback,
  one line: no-op under `testing`, moot under lifespan — but it saves
  lifespan-less writers: data-fix scripts and REPL kernels that persist
  would otherwise enqueue into a queue nothing drains) then
  `_save_queue.put(item)`. Nothing else to reason about on the hot path.
- Single consumer (daemon thread `dag-save`) drains FIFO and calls
  `_save_node_result()`. `queue.Queue` is the only shared state; FIFO
  preserves cascade order in the append-only `node_results` history, which
  `node_result_before()` and `dep_timestamps` freshness depend on.
- **Writer lifecycle owned by lifespan** (same pattern as `start_processor`):
  - Startup: `_ensure_save_thread()` (no-ops under `testing`). Invariant:
    the app serves ⇒ the writer exists — established before the first
    request, not emergent from first traffic.
  - Shutdown: `_save_queue.put(None)` (sentinel), worker drains remaining
    FIFO then exits, `thread.join(timeout=10)`, log-and-proceed on timeout
    (wedged disk must not hang `systemctl stop`; remainder is lost, same
    trade as a crash, but visible). The worker's sentinel branch already
    exists — it is just never sent today. This drain is what protects
    deploys: SIGTERM → graceful uvicorn shutdown → lifespan teardown.

## Test strategy

The queue is REAL in tests — tests mimic production, deterministically.

- `enqueue_save()` always enqueues. The `testing` flag gates only thread
  start (lifespan's `_ensure_save_thread()` no-ops; no thread in tests).
  The flush runs on the test thread — which is also why `:memory:` keeps
  working.
- **`flush_all()` gains the save-queue drain** — it already exists, already
  means "make everything landed", and is already called by exactly the
  tests that assert durable state. Ordering inside: process the recompute
  queue to exhaustion first (processing enqueues saves), then drain saves.
  Two loops, no waiting, no sleeps.
- **Fixture setup clears the queue** (isolation, not semantics): a leftover
  queued write from test A must never land in test B's fresh in-memory DB.
  Fixture teardown uses the internal unguarded drain.
- **No-op flush guard** — the wrong patterns (flush-per-write, double
  flush, "flush so my read can see writes") all collapse to one signature:
  a flush that drains nothing where nothing was enqueued since the last
  flush. The conftest-level flush raises on that:

  ```
  flush_all() drained nothing and nothing was enqueued since the last
  flush. flush_all() is a once-per-operation drain — writes land
  asynchronously by design and reads never wait on the queue (WAL snapshot
  + timestamp predicates). See docs/dag-library.md →
  'Persistence: reads, writes, and the save queue'.
  ```

  Legit multi-flush tests (apply → flush → assert → restore → flush →
  assert) never trip: each flush drains real work. The guard lives only in
  the public `flush_all()`; persistence stays prod-clean.
- **Existing tests are unchanged** — the suite already flushes at the right
  granularity. Exception: genuinely wrong tests (the what-if tests, built
  without the architecture in mind, then duct-taped with inline flushes)
  are fixed as wrong: operation → one `flush_all()` → assert, duct tape
  deleted.
- Worker/queue unit tests are optional — concurrency itself is not
  test-verified (requirement 3).

## What changes

| File | Change |
|---|---|
| `dag/persistence.py` | Save queue + worker + sentinel drain; `_save_node_result` privatized + writer-thread guard; `enqueue_save()` = lazy-start fallback + one `put`; internal unguarded drain for fixtures |
| `server.py` (lifespan) | Startup: start writer. Shutdown: sentinel + bounded join |
| `tests/unit/conftest.py` | `flush_all()` drains saves after recompute; no-op-flush guard with doc pointer |
| `tests/unit/isolation_fixtures.py` | Clear save queue at setup; internal drain at teardown |
| `docs/dag-library.md` | New section: "Persistence: reads, writes, and the save queue" |
| Cleanup | Delete route-handler `flush_pending_saves()` (`api_router.py` what-if restore); delete sprinkled per-test flushes; fold the queue machinery out of `dag/node.py` (PR #94 leftovers) into `persistence.py` |

## What does NOT change

- `_persist()` signature and call sites (DerivedNode.refresh,
  UserInputNode.push).
- Reads — `latest_attempt()`, `latest_node_result()`,
  `node_result_before()` — and their callers' logic.
- The broadcaster, the API routes, the frontend.
- The existing test suite's flush discipline.

## Accepted trades

**Crash window.** Writes sit in the in-memory queue until drained; a crash
inside the window loses those rows (they were never issued to SQLite — WAL
is irrelevant here). The pre-change system bought durability by writing
synchronously on the event loop; this change sells that durability back,
which is exactly what requirement 1 demands. Costs, bounded:

- Derived nodes: lost row → reload pending/stale → staleness machinery
  schedules recompute. Self-healing; costs recompute time (docs already
  normalize "first boot may take tens of minutes to settle").
- User inputs (settings, what-if): lost edit silently reverts. Coherent,
  no torn state — the marker and the values it gates are queued together,
  and after restart the in-memory marker is gone anyway.
- Window: milliseconds — the writer drains FIFO continuously; the queue is
  only deep mid-cascade. Real exposure: crash-mid-cascade.

Decision: accept. Single-family app, DB-file backups already exist
(`docs/deployment-oracle-free-tier.md`); the alternative (sync writes for
user inputs) reintroduces a second write path — the complexity this change
exists to remove — to insure against a sub-second risk.

## Reasoning summary

One writer thread writes; everything else enqueues and never blocks. Reads
are either memory-current or timestamp-predicated history that excludes
in-flight writes by construction, and any stale read is corrected by a
push. The queue has exactly two observers (producer, consumer) plus a
shutdown drain; the writer's lifecycle lives in one function pair
(lifespan start/shutdown). Tests drive the same path synchronously at the
choke points the suite already uses, and a no-op flush fails loudly with a
pointer to the docs. Nothing else is load-bearing.
