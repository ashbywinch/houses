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

## Sequencing

A (library first: A1 → A2 → A3 → A4) → B backlog items in user-approved
order → walkthrough re-run after each batch → full suite
(`make test` + language sweep) before every push.

## Verification

- `make test` green; ruff + basedpyright clean; language sweep green.
- Two-phase walkthrough re-run as the final acceptance for any UX batch
  (participant confusions gone / downgraded).
- PR: all work lands on `feat/settings-screen` (PR #53) and goes through
  the pr-review loop until findings add little value.
