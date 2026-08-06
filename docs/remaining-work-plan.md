# Remaining Work — Uncertainty in the DAG Library & Ongoing Usability Testing

Status: plan (2026-08-04). Audience: the next agent picking up this work.
Read this first, then the linked docs. All work below follows the repo's
red/green TDD rule and the requirements baseline
[docs/usability-requirements.md](usability-requirements.md) (principles
P1–P13).

## Where things stand

- Settings screen (Phase 1 of the isochrone plan), round-1 UX fixes, and
  round-2 workstreams W-B (selling-home toggle), W-C (standard
  ProvenanceToggle + deposit provenance), W-A (header person/settings
  menu, commute CRUD), W-E (council-tax copy) are all implemented,
  tested, and pushed on `feat/settings-screen` (PR #53).
- The architecture test was fixed (it was vacuous: `./`-anchored globs
  never matched archunitpython's absolute paths); `PMT`, `StampDutyFn`,
  `TieredRate` moved out of `dag/expression.py` into
  `houses/nodes/expressions.py`. `dag/` is now houses-agnostic.
- Usability walkthrough run 2 (round-2 findings) drove the work above;
  run 3's de-primed two-phase walk produced a fresh findings set (C2–C11
  below). The two-phase prompt lives at
  [docs/ux-walkthrough-prompt.md](ux-walkthrough-prompt.md).

## Part A — Uncertainty ("≈") as a first-class DAG citizen

Settled design decisions (from the design chat — do not relitigate without
the user):

- **Library support, not houses-specific code.** Approximate values belong
  in the `dag/` library, completely independent of the houses project —
  any node in any project can produce one.
- **Built on the `uncertainties` package** (ufloat-style: value + stddev,
  with operator-defined propagation). The DAG's value wrapper (e.g.
  `Measurement[T]` or a thin wrapper around `uncertainties.ufloat` for
  numeric amounts) defines `__add__`/`__sub__`/`__mul__`/`__div__` so the
  SAME operator combines the values and the errors — code is obviously
  correct by construction. This is the `uncertainties`/dual-numbers model.
- **Precision-aware, not conservative**: propagation follows actual data
  dependency. Selection nodes (`Choose`, `IfThenElseNode` — both already
  generic in `dag/`) need NO uncertainty-specific logic: they return the
  chosen branch's value as-is, so the winner's uncertainty travels and the
  loser's never enters. This is why selection nodes were flagged as
  critical — under the wrapper they're trivially correct.
- **Provenance stays in provenance.** The uncertainty is a property of the
  VALUE (the wrapper), not stuffed into formula lines. Provenance explains
  how; the value carries what it is.
- **Council-tax fallback (houses side)**: when the lookup fails, the node
  returns an estimated yearly cost with a spread, rendered as
  **"Band: ? (£1,200 ± £50)"** — band unknown, cost an estimate. The UI
  renders the P2 rule: `≈` at face value, one-step reason
  ("council tax estimated — address lookup failed").
- **Exact = zero uncertainty**, so estimates never quietly become facts
  downstream and verified figures are untouched.

Workstreams (each red/green):

1. **A1 — library Measurement wrapper**: `dag/measurement.py` with a
   generic value+uncertainty wrapper built on `uncertainties` (numeric
   core); arithmetic operators propagate; `to_json`-friendly serialization
   (`{value, uncertainty}`); unit tests for +, −, ×, ÷ propagation and
   exact+approximate mixing. Acceptance: a node returning a measured value
   flows through `Ref`/`Add`/`Choose` with correct uncertainty; exact
   inputs stay exact.
2. **A2 — selection-node check**: tests proving `Choose` and
   `IfThenElseNode` return the chosen branch's measurement unchanged (no
   propagation from the loser) — the precision-aware guarantee.
3. **A3 — council-tax node fallback**: on lookup failure, return
   Band D estimate with a spread instead of plain "?"; provenance notes
   the fallback; `total_monthly_cost` inherits `≈`; regression tests for
   the node and the total.
4. **A4 — UI**: card/detail render `≈ £X/mo` when the total is
   approximate (title: the one-step reason); Costs page shows
   "Band: ? (£1,200 ± £50)" for the estimated council tax. Language sweep
   stays green (P6).
5. **A5 — propagate to filtering/sorting** (only if the data issue is
   revisited — see Part B note): sort/filter on estimates when they exist.

## Part B — Ongoing usability testing

- **Cadence**: after each UX change batch, re-run the two-phase walkthrough
  (participant → evaluator) as the acceptance gate (P13). The evaluator's
  findings become the next backlog.
- **Backlog from run 3** (evidence in the evaluator report; each is a
  candidate workstream, red/green, one at a time with user sign-off):
  - C2 — no live preview when editing settings (commute/sale price). NOTE:
    user decision pending on mechanism (server dry-run vs accept save-then-
    adjust; the DAG propagates correctly after save).
  - C3 — no unsaved-changes indicator; edits silently revert on refresh.
  - C4 — old-office commute on cards has no staleness signal.
  - C5 — sinking fund £7,800/yr vs £433/mo looks inconsistent (the ⅔ split
    is never explained) — provenance/copy fix.
  - C6 — no plain-language sentence for what "Total Monthly" includes.
  - C7 — commute pill colours have no legend.
  - C8 — Map tab renders a bare price list, not a map.
  - C9 — "worst acceptable commute" setting has no visible effect on the
    list.
  - C10 — "Cost of Works" timing unexplained.
  - C11 — no area/address search.
- **Explicitly out of scope**: C1 — the £?/mo prevalence (38/40 houses) is
  a known DATA issue (council-tax lookup failures at scale); the tooltip
  and address-edit fix-path are shipped; the root cause is tracked
  separately and its fix requires the user's sign-off (it touches the
  calculation).
- **Not built yet (from earlier plans, keep in mind)**: isochrone plan
  Phase 1 items 4–5 (worst-commute ceiling filter + "worst commute < X"
  slider, weekly-commute sort) and Phase 2 (generation runner/panel) —
  see [docs/website-isochrone-integration.md](website-isochrone-integration.md).

## Part C — Architecture/test hygiene (done, keep it that way)

- Layer patterns in `tests/unit/dag/test_architecture.py` are
  runtime-derived absolute paths (see the test and the
  `archunitpython-glob-rules` skill). Never reintroduce `./`-anchored
  globs — they make the check vacuous. Sanity rule: verify a known
  violation goes red before trusting a green architecture test.
- `dag/` must stay houses-agnostic: new domain expressions belong in
  `houses/nodes/expressions.py`, not `dag/expression.py`.

## Part D — What-if / scenario evaluation (IMPLEMENTED 2026-08-06)

D1 (library `dag/evaluate.py` — task-local staged evaluation,
node-keyed overrides, incremental recompute of only the changed
subtree) and D2 (houses `POST /api/what-if` + the "What if…" panel on
the property list; override catalog = the `persons` settings node,
which carries every editable field: money, selling-home toggle,
`trips_per_week`) are implemented and tested. What remains open: the
library's original redesign ambition (full structure/state separation
for forking graphs) is NOT needed for the current catalog — `evaluate`
stages overrides through a ContextVar read-hook instead. Revisit only
if a what-if needs to fork a graph mid-session.

Why the current design isn't conducive (grounded in `dag/`):

1. **Push, not pull.** The runtime is a reactive scheduler
   (`AsyncQueueScheduler`): input change → invalidate → recompute →
   persist → signal → downstream invalidation. There is no "evaluate
   node X under inputs with override δ, throwaway" path.
2. **Identity is global persisted state.** Nodes are keyed by ids
   (`settings/…`, per-rid nodes) and values live in `node_results`; a
   candidate value has no representation. Mutating a real input node
   fires real invalidation + persistence and needs rollback — the
   "delete state" antipattern.
3. **Provenance is a persistence record** (timestamped `Attempt`s).
   What-if provenance must say "hypothetical input, not saved" — the
   `SourceType` enum doesn't model that.
4. **No snapshot/fork.** The graph is a live object; there is no cheap
   "same structure, different inputs" instantiation.

Redesign sketch (library work, houses-agnostic — mirrors Part A's rule):

- Separate graph **structure** (edges + compute) from **state** (values)
  so a scenario = same structure + input overrides.
- Add a synchronous, memoized, side-effect-free pull evaluation of the
  dependency closure (`evaluate(graph, targets, overrides)`), reusing
  `DerivedNode.compute`, producing throwaway `Attempt`s. Persistence
  stays entirely out of this path; the existing scheduler/persistence is
  unchanged for the real path. Overrides are keyed by node id
  (`node_id → candidate value`); which nodes are override-able is
  registered per project (houses side), keeping the library generic.
- Provenance marks overridden inputs so the UI can render "hypothetical".
- **Synergy with Part A**: a `Measurement` wrapper flows through the same
  pure evaluation, so a what-if over an uncertain input yields a range
  ("if we sell around £600k, monthly cost lands £1,200–£1,400") — the
  honest way to present a hypothetical. Tests also gain a DB-free
  evaluation path.

UX direction (settled 2026-08-06):

- A what-if is a QUESTION; settings is the FACTS form. Live-preview
  inside the settings form blurs "is this number true or hypothetical?"
  and the interesting consequences are house-shaped (which houses fall
  under £X/mo), not settings-shaped. Financial what-if tools (Relm,
  ifso, calculators) keep "play" separate from committed state and
  emphasize deltas over absolutes.
- The what-if lives in a "What if…" panel on the property list (where
  the consequences are). Ephemeral by design (refresh → gone), single
  clear exit + "apply to settings" escape hatch (existing PATCH path),
  delta framing ("3 more houses under £1,500/mo") over new totals,
  provenance shows the hypothetical input through the existing
  `ProvenanceToggle`.
- The panel's input catalog is DECLARATIVE and extensible — driven by
  a list of override-able DAG inputs (node id + label + unit + current
  value), not hardcoded fields. v1 ships: selling-home toggle + money
  fields (sale price / mortgage / cash) + commute frequency
  (trips_per_week per person/POI). Later additions must slot in without
  redesign: life insurance, the works estimate, and any other spend
  the user could choose not to make. Mechanics for per-property
  derived spends (e.g. zeroing the works estimate) are open — the
  catalog requirement is that they must be representable.
- Excluded by design: destination/address changes ("what if I worked
  somewhere else") — they trigger routing API calls. The commute
  what-if is frequency-only ("what if I went in one day a week").

## Part E — Walkthrough run 4 (2026-08-06)

Two-phase walkthrough re-run (P13) against the live app: participant
`UxParticipant2` (16m walk, 22 screenshots), evaluator `UxEvaluator`
(report in agent://UxEvaluator). Confusions by severity: two blockers
(old-office commutes + "Can't calculate" totals), three highs, five
mediums/lows.

Fixed in PR #54 (this session):

- Commute-ceiling filter: default OFF (it hid ALL houses on first
  visit), store-persisted toggle (no longer silently re-applies on
  navigation), plain-language chip showing the hidden count.
- Detail-page bare "?" commute pills → "No route" with a tooltip.
- Card "£?/mo" → "£—/mo" (the "?" read as "£7/mo" at a glance).
- Favourites view gets a heading + "N saved houses" count.
- "What if…" panel collapsed by default (was a wall of family
  numbers above the houses on first visit).
- Cards get a "Change destinations →" link (the only path was buried
  on the detail page).
- Costs page explains very high commute figures (TfL daily maximum).

Deferred / needs a user decision:

- ~~Run-4 blocker "Total Monthly — Can't calculate" everywhere~~ —
  RESOLVED 2026-08-06 via `POST /api/admin/regenerate` (force
  recompute of code-stale nodes; `{"patterns": ["*/council_tax"]}`):
  all 40 council-tax nodes regenerated, 16 totals unblocked. The
  remaining 24 impossible totals are "Works estimate required for:
  Ashby" — real settings data the family hasn't entered, not
  staleness (enter per-property works estimates in Settings).
- "Max Monthly Outgoings" offered while totals are unavailable (same
  works-estimate data gap).
- Optional polish: favourites distinctness beyond the heading.

## Part E — Walkthrough run 5 (2026-08-06, P13 loop after run-4 fixes)

Re-run after the run-4 fixes + the council-tax regeneration: participant
`UxParticipantRun2` (19m walk, screenshots in /tmp/ux-houses-run2/),
evaluator `UxEvaluatorRun2` (report in agent://UxEvaluatorRun2). 13
confusions; triaged:

Fixed in PR #54:

- Commute-limit chip: no longer offers to hide when it would hide EVERY
  house (info chip "All N houses are over the family's commute
  limit — change the limit in Settings"); empty state guides the user
  back to the chip.
- "Total Monthly — Can't calculate" now names the missing piece (leaf
  reason, e.g. "Works estimate required for: Ashby").
- Council Tax estimate label: "? · (£1,200 ± £50)/yr" → "Band unknown ·
  (£1,200 ± £50)/yr".
- Sinking-fund note now uses THIS property's actual figures (was a
  static £7,800/£433 example that never matched).
- Commute "how is this calculated?" drops petrol sources for non-car
  routes (the provenance walks every mode branch).
- Cap fares show inline "(max)"; school walks say "min walk" not "m
  walk"; filter label "Max Monthly Outgoings" → "Max monthly cost".

Verified NOT reproducible — CONFIRMED against the run-2 screenshots and
the live DOM (screenshot-first, per the walkthrough protocol):

- "Mortgage missing from Costs tab" — refuted: the row is the FIRST
  costs row and renders "Mortgage £1,080.94"; a fresh Costs-tab click
  lands it at the top of the viewport (verified live in the DOM).
  Screenshot 04/05 captured scrolled positions.
- "Pimlico missing from Commute tab" — refuted: the DOM renders
  Simon/Pimlico as the first accordion item (all 6 commutes present).
  Screenshot 06 captured a scrolled position.
- "Red wavy underline on monthly cost" — refuted: screenshot 01 shows
  plain green figures (`≈£2,244.59/mo`, `£—/mo`) with no underline,
  and no such style exists in the code (likely a browser-spellcheck
  artifact in the participant's viewport).

Residual UX kernel (no code bug): the participant scrolled past the
top rows without noticing them — the mortgage (largest cost) and
Pimlico (first commute) don't stand out visually. Prominence polish
only; both are already the first rows.

Deferred (data/scope): Summary tab describes the town, not the house
(no photos/bathroom data in the DAG); map marker clustering + legend;
favourites distinctness beyond the heading.

## Sequencing

A (library first: A1 → A2 → A3 → A4) → D (library `evaluate` primitive,
then the houses override catalog + "What if…" panel) → B backlog items in
user-approved order → walkthrough re-run after each batch → full suite
(`make test` + language sweep) before every push. A before D: Part A's
`Measurement` gives what-if range rendering, and the `evaluate` primitive
builds on the same pure-evaluation path (settled 2026-08-06).

## Verification

- `make test` green; ruff + basedpyright clean; language sweep green.
- Two-phase walkthrough re-run as the final acceptance for any UX batch
  (participant confusions gone / downgraded).
- PR: all work lands on `feat/settings-screen` (PR #53) and goes through
  the pr-review loop until findings add little value.
