# Provenance frozen-row rebuild — plan

## Goal (user's words)

Provenance must ALWAYS render the exact values originally calculated and
stored — never a re-run calculation, never a live re-read of a dep, never
a swallowed exception. And: no partial fix with remaining work scattered
around the codebase.

Three architectural rules (user's corrections, non-negotiable):

1. The row already stores the full tree zlib-compressed. Do not invent a
   parallel design beside it.
2. Deps are nodes. No lambdas, no dicts, no provider closures, no manual
   `get_scheduler().schedule(...)` in node owners. If the input set changes
   at runtime, rewire with `set_deps(...)` — which takes nodes and rewires
   their signal slots. Fix the docs to say this.
3. A node must not read state from nodes it has no dep edge to. Deps define
   who gets updated when.

## Part A — migrate GOOD changes to a fresh branch

Fresh branch from `main`. NOTHING on the current branch is cherry-picked:
`59a0617` and `5d44201` carry the `DepInputs` envelope, so cherry-picking
them imports the parallel design we are deleting. Every kept change is
re-applied by hand on the fresh branch, minus the nonsense:

1. Dep-narrowing (the small correct part of `59a0617`): planners depend on
   the `DestinationAddressNode` projection, not the full POI. A trips-only
   edit changes the place but not its address, so planners never go stale
   and never re-plan. No reuse helper, no stamp comparison, no apparatus.
2. `Commute.origin` field (`0ebad2b`): the planned origin recorded on the
   value at plan time. Display fact only — provenance may show where the
   journey was planned from. NOT a reuse input: staleness already decides
   reuse-vs-replan, and `compute` must not second-guess it. (What `compute`
   needs arrives as a dep; the dep graph already made that decision.)
3. Docs rule `6d4c6e6` (never store derived state outside the value). KEEP.
4. `a0bb0c6` split: KEEP `monthly_delta.py` + `CostsSection.vue` + types +
   frontend tests (vs-row provenance); `PropertyList.vue` + test (controls
   wrap); `domain.py` POI projection (zeroed destination named); the
   zero-day test file (contract: 0x shown, 1x never). DROP
   `_patch_commute_frequency` and its call site — regex rewriting of
   already-built provenance is the wrong-layer fix.

NOT carried in any form:

- `DepInputs` (dataclass, `_project_dep_inputs`,
  `_stored_input_provenance`, `_stored_subtree`, every `dep_inputs=` arg,
  every `_stored_dep_inputs()` reader, `test_dep_inputs.py`). No parallel
  store beside the row, ever.
- `_reusable_journey` / `_origin_key` (`ad899ec`), `_restamp_needed` /
  `_restamp_refresh` + refresh gate + manual fan-out (`e6071e6`).
- `_patch_commute_frequency` (`a0bb0c6`).
- The `79c77bf`–`3c2dd9d` docs describing the above mechanisms.
- The entire uncommitted tree on `fix/provenance-mobile-commute`
  (~22 modified files): scheduler drain rewrites, recursive deps-first
  `refresh()`, `served_formula`, test-reader conversions. Rebuild clean
  per Part B.

Stray-file rule: no `test_zz_*.py` probes left behind. Verify with
`git status --porcelain | grep '??'` before every commit.

## Part B — the fix is small; the audit is the work

Scope boundary (user's corrections, non-negotiable):

- The rescheduler reschedules on STALE deps, not only pending ones. If a
  node's deps are stale it does not run `compute()` — it requeues after
  they complete. (Pending-only requeue is the current gap:
  `dag/scheduler.py:_requeue_after_deps` fires on `attempt.pending`
  alone. A succeeded-but-stale dep trips no guard.)
- `compute()` reads its bound attempts. `latest_attempt()` inside
  `compute` (or inside a `provenance_formula` on the serve path) is a
  live re-read — forbidden. (User's proof: with correct
  stale-rescheduling, a dep cannot hold a newer push at compute time, so
  the bound attempt IS the current one; reaching past it is always wrong.)
- Deps must be EXACTLY what `compute` reads — no wider, no narrower. A
  node holding a dep's PARENT as a member (`self._persons_source`) while
  reading past it with `latest_attempt()` is the bug shape (live on
  `main`: `DestinationPlaceNode.compute` reads
  `self._persons_source.latest_attempt()` instead of its bound
  `persons` attempt; `WalkNode` carries a dangling comment about a
  patch that no longer exists). Fix the dep list, never bodge around it
  with a longer reach. NO long-winded workarounds for things the DAG
  already does.
- NEVER add a parallel store beside the row. Serve is already verbatim
  on `main` in the no-arg path — confirm, don't rebuild it.

Steps (in order, each committed separately):

1. **Docs first.** Bring `docs/dag-library.md` up to date with everything
   learned: serve-verbatim, stale-reschedules (not just pending), deps
   exact (the `DestinationPlaceNode` shape as the named wrong example),
   no `latest_attempt()` outside tests/seams, no derived state outside
   the value. Style per `docs/writing-documentation.md`: concrete
   wrong-fix code, no history lessons, no PR numbers.
2. **Audit every node against the rules** (this is the bulk of the work).
   Findings so far (earned by reading `main`, start list — anything
   unread may hold more):

   `compute()` reaching past its bound attempts (`latest_attempt()` —
   forbidden; the bound attempt IS the current one once stale-rescheduling
   holds, so the reach is always wrong):
   `transit.py:244` (`DestinationPlaceNode.compute` ignores its bound
   persons attempt, re-reads `self._persons_source`);
   `commute.py:439,444` (merge formula re-reads both results);
   `petrol.py:136-137` (mpg, cost); `total_works_node.py:25`,
   `life_insurance_total_node.py:24`, `monthly_sinking_fund_node.py:31`,
   `area.py:90`, `park_and_ride_augment_node.py:91`,
   `stamp_duty_node.py:36`, `bus.py:297`;
   `total_monthly_housing_cost_node.py:259,270,273` (+ `_stored_names`
   helper); `commute_breakdown_node.py:149` (live-read fallback when the
   positional bind misses — the miss must fail loudly, not fall back).

   Dep set decided by live peek instead of the bound evaluation
   (`_get_active_deps` / expression predicates calling
   `latest_attempt()`): `commute.py:459` (merge conditional fare dep);
   `bus.py:286,297` (`_current_max_walk`, transit-input peek);
   `total_monthly_housing_cost_node.py:117,124,133-135,147`;
   `location.py:45`.

   Dep list wider than `compute` reads: `WalkNode`/`DriveNode` (full POI
   dep, address-only plan — narrow to the `DestinationAddressNode`
   projection). Breakdown node: dict-held selectors + provider-style
   wiring — must hold its selector nodes as deps and rewire with
   `set_deps(...)` (which takes nodes, never tuples-as-data, never
   lambdas) on persons change.

   Manual scheduling: `property_nodes.py:582`
   (`schedule(self.commute_breakdown)` in `_on_persons_changed`) — delete;
   deps + signals do this work once the breakdown holds real deps.
   Exception (agreed): `dag/regenerate.py:90` (`schedule_code_stale_nodes`)
   stays — a deploy emits no `changed` signal, so no dep wiring can ever
   enqueue code-stale nodes; the startup sweep is the only path. Rule:
   scheduling around a data signal that already exists is forbidden;
   scheduling where no signal can exist (code version changed under
   persisted data) is the admin path.

   Fix shape: narrow/widen the dep list so it is EXACTLY what `compute`
   reads, then read the bound attempts. Never reach further. No
   workarounds for things the DAG already does.

   Explicitly NOT violations (checked): `latest_attempt()` inside
   `dag/expression.py`, `dag/evaluate.py`, `dag/eval_context.py`,
   `if_then_else_node.py:57` (the bound-attempt machinery itself);
   `property_nodes.py:333-337,523`, `settings.py:201`,
   `settings_node.py:86` (construction/admin-time reads, not
   `compute()`/serve); the `latest_attempt()` definition itself.
3. **Stale rescheduling.** Extend the requeue path so a node whose deps
   are stale-but-succeeded defers the same way a pending dep does. Small,
   scheduler-local, no drain rewrite, no refresh-order change.
4. **Serve-verbatim confirm.** Verify `main`'s no-arg `build_provenance`
   serves the frozen row with no dep-row join and no live build. If it
   already does, record that in the docs and move on — no change.
5. **Dep-narrowing + selector stamp** (re-applied `59a0617` minus
   `DepInputs`): address projection, selector `replace(val,
   destination=poi_val)` from its OWN bound `poi` dep
   (`commute.py:364-366`).
6. **Full gate, then commit + push** to the fresh branch (backend pytest,
   ruff check + format, pyrefly-lock, frontend vitest + vue-tsc). Zero
   failures before push. Never commit to `main`; never `rm data/houses.db`;
   never restart/kill the dev server or `fuser -k 8080/tcp`.

## Verification

- `grep -rn "latest_attempt()" houses/nodes/ --include="*.py"` → only construction/admin-time reads remain (each justified inline); zero in `compute()`, `provenance_formula`, `_get_active_deps`, expression predicates.
- `grep -rn "get_scheduler().schedule" houses/nodes/ dag/derived_node.py --include="*.py"` → empty (`dag/regenerate.py` startup sweep only).
- `git status --porcelain | grep '??'` → empty (no probes).
- Full suite green; frontend green; PR checks green.
