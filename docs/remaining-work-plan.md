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
  - C5 — ~~sinking fund £7,800/yr vs £433/mo looks inconsistent (the ⅔ split
    is never explained)~~ DONE: the ×⅔ fudge is removed (monthly = yearly ÷ 12)
    and the note now reads "£X/mo is the yearly fund split across 12 months".
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

## Part E — Walkthrough run 6 (2026-08-06, P13 loop after run-5 fixes)

Re-run after the run-5 fixes: participant `UxParticipantRun3` (13m walk,
screenshots in /tmp/ux-houses-run3/), evaluator `UxEvaluatorRun3` (report
in agent://UxEvaluatorRun3). 8 confusions; triaged:

Fixed in PR #54:

- Raw TfL "HTTP 404: {$type: ...}" blob was rendered on a "Can't
  calculate" total — a regression from the run-5 leaf-reason fix. The
  route-merge node (TransitNode) now attaches a friendly user_message
  ("Couldn't find a route to this destination — check the address.")
  while keeping the raw error in the internal message for logs.
- Commute-limit chip: the all-hidden info chip no longer renders a
  dead "×" (clicking it did nothing); copy now explains the worst-
  commute judgement ("All N houses have a commute over the 45-minute
  limit — the worst commute counts").
- Map pins: enlarged hit target (padding + negative margin).
- Commute accordion: an expanded no-route row shows "No route found
  for this destination — check the address in Settings." instead of
  an apparently-empty body.

Not code-fixable / inherent:

- "Commute is to the old office" — the app cannot know a destination
  is stale until the family updates it in Settings (protocol-
  constrained walk: the participant was told not to edit inputs).
- "Who is Ashby?" — real family data; the settings page shows names
  and badges but not relationships.
- Works-estimate blocker names the missing person (C1 satisfied) but
  only that person can enter it (ownership) — no inline path for
  others, by design.

## Part E — Card-band snag (2026-08-06, user report)

"House cards have the coloured band on two sides and they're not all
the same colour on the same card — I don't know what either one
represents."

Root cause: the card had TWO silent colour axes. (1) Top status bar =
worst-commute severity (green ok / orange tight / red far — the palette
the list-header legend already documents, but nothing connected the bar
to it). (2) Left accent border = triage state — and it was broken in
three ways: the default "active" state painted EVERY untouched card
green (a second band with no meaning); the palette collided with the
status axis (amber favourite ≈ orange tight, red dismissed = red far);
and favourite was amber while the favourite BUTTON was blue — the
border didn't even match its own axis.

Fixed:

- Border appears only for real triage states (favourite/dismissed/seen);
  untouched cards have no border. One meaningful band per card max.
- Border colours now match the triage buttons exactly (favourite=blue
  accent, dismissed=red, seen=green) — one axis, one palette, and the
  favourite border is no longer confused with the commute palette.
- Both bars carry a hover title ("A commute is too far", "Favourite",
  …) so a colour is never unexplained.
- Status bar bug: a card whose commutes ALL had no route showed GREEN
  (the worst-severity loop initialised at 0); now muted gray
  (`--commute-none`), matching the legend's "no route" dot.

Verified: computed styles on the live app (favourite border =
rgb(45,106,79) = --blue accent; default border = transparent; status
bars red/orange), plus 3 new PropertyCard tests (default has no
border, triage borders + titles, severity classes + titles) — 221
frontend green.

## Part E — Settings overhaul + settable-audit (2026-08-06, user report)

"Clicking Settings is highly confusing. It should just show you your
settings. … What's left that should be user settable somehow but isn't?
Either user nodes in the dag or things like those commute colour bands."

### Settings page

- The page showed EVERY family member's full settings (read-only for
  others) plus the deposit. Now it shows ONLY the session person's
  settings: impersonated person (superuser mode) → session person
  (server email linkage) → session display name (the DAG keys people by
  NAME, so the Google/device profile name is the identity when the
  email isn't linked — the dev login email simon@example.com doesn't
  match the person record's smwinch@gmail.com). Truly-unlinked sessions
  still see everyone read-only.
- The header "Settings ▾" drop-down (which confusingly listed every
  family member) is now a direct Settings link. The per-person
  ?person= deep-links from the old menu are gone; the property page's
  "Change destinations →" still works (it links the session person).
- The deposit summary stays (it is the readout of the money fields).
- NEW settable fields: "Commute is easy up to (minutes)"
  (good_max_minutes — the green→amber band) and per-destination
  "Weeks per year" (was silently defaulting to 46).

### Settable-audit (user DAG nodes vs UI)

| Node | Fields | UI |
|---|---|---|
| persons | has_car, selling_home, sale/mortgage/cash (whole £), life insurance, destinations (label/address/trips/weeks/modes) | Settings ✓ (what-if overrides sale/mortgage/cash) |
| commute_thresholds | fine_max_minutes ✓ ("worst acceptable") · good_max_minutes (NEW) | both now in Settings ✓ |
| financial | mortgage_rate, mortgage_term_years, sinking_fund_rate, petrol_mpg, petrol_cost_per_litre, working_weeks_per_year, rental_income_monthly (legacy dupes: current_home_*, gross_ashby_contribution) | **NO UI** — live in the DAG (feed the monthly total) but only PATCHable via API. Candidate for a household-finances section; NOT built (changes the monthly-total math — needs sign-off). |

Built 2026-08-06 (second pass): the 5 LIVE financial fields now have a
"Household finances" section on the settings page (mortgage rate %,
term, sinking-fund %, petrol MPG, petrol cost £/l), autosaved via
PATCH /settings/financial with percent↔fraction conversion. The GET
/settings financial blob previously came from a legacy dict node that
went STALE after any PATCH (the DAG always read the individual
setting nodes) — it now serializes the live nodes
(settings_node.aggregate_dict); the legacy `financial_source` field
and `make_default_financials` are removed. The remaining financial
keys (working_weeks_per_year, rental_income_monthly,
current_home_*, gross_ashby_contribution) have NO DAG consumers —
dead defaults, deliberately not surfaced.

### Colour bands now actually work

The card and detail-page pills previously used hardcoded thresholds
(15/45 walk, 45/75 non-walk) — even the "worst acceptable commute" in
Settings did NOT change the pill colours (only the hide-over-ceiling
filter used it). Now pills resolve per person from the settings node
(good = green→amber, fine = amber→red), so the bands are real: Simon
30/45, Lorena 40/60.

Verified live: settings page shows only Simon's section; good/fine
bands + weeks inputs present; 229 frontend tests green.

A (library first: A1 → A2 → A3 → A4) → D (library `evaluate` primitive,
then the houses override catalog + "What if…" panel) → B backlog items in
user-approved order → walkthrough re-run after each batch → full suite
(`make test` + language sweep) before every push. A before D: Part A's
`Measurement` gives what-if range rendering, and the `evaluate` primitive
builds on the same pure-evaluation path (settled 2026-08-06).

## Part E — Settings implementation (2026-08-07, approved model changes)

The tabbed prototype (Finances | Commutes) is implemented in the real
app with all the approved model changes:

- Transit rename ('train' -> 'transit' everywhere: value, label, API,
  migration rule, selector; persisted persons migrated live via
  tools/migrate_train_to_transit.py). Journey-LEG mode names
  (train/tube/bus...) deliberately untouched.
- Petrol MPG is per-person (Person.petrol_mpg, PersonPetrolMpgNode);
  petrol cost/litre stays a household finance.
- Max walk exposed: bus_walk_penalty was already in the model + API —
  now a 'Willing to walk up to (minutes)' field on the Commutes tab.
- Deposit excludes children completely (pure _deposit_breakdown helper,
  unit-tested; EquityTotalNode skips children too).
- Settings page: Finances tab (default, left) = deposit + household
  finances + money fields; Commutes tab = has-car + MPG + bands +
  max-walk + destinations. Shared WholePoundsField component extracted
  from the what-if panel. Copy: 'Transit', 'Transport modes you'd
  accept', no 'office', no leave-address-blank labels, future-house
  framing intro.

Verified live at 375px (tabs switch, MPG under has-car, max-walk,
modes labelled Transit/Driving/Walking, trips+weeks present).
234 frontend + 1410 python green.

## Part E — Destination-in-legs + detail summary bar (2026-08-07)

User report round (P13 loop): (1) the walk/drive destination belongs
IN THE DAG LEGS, not rendered on the commute accordion header; (2) the
school walk destination must be the school's ADDRESS, never a bare
lat/lon; (3) the two monthly costs sit on the right, each on its own
row; (4) "3 bed" goes on the line below the price, left-aligned;
(5) the edit-address link must look grouped with the address, using the
standard classes; (6) verify the user is actually a superuser; (7) make
the troubleshooting screenshot representative of the user's own view.

- School address captured at load: `School.from_GIAS_row` now joins
  Street/Locality/Address3/Town/County/Postcode into `full_address`;
  Primary/SecondarySchoolNode emit `postcode` + `full_address`;
  `SchoolLocationNode` prefers `full_address` → `"{name}, {postcode}"`
  → `lat,lon` (last resort). Live school legs now read
  "129 Upper Woodcote Road, Reading, RG4 7LB" / "Surley Row, Emmer
  Green, Reading, Berkshire, RG4 8LR".
- `_google_route_commute` sets `end_station=dest_str` on walk/drive
  JourneyLegs; the travel planner owns the destination, not the UI.
  `CommuteSection.vue` dropped the accordion-header destination overlay
  and its CSS.
- Detail summary bar: `.summary-address-row` is a plain block with a
  bottom border; the "Edit address" button now flows inline at the end
  of the address heading (wraps with it — a flex row could never put a
  button on the last line of a wrapping h1). `.summary-facts` is a
  `1fr auto` grid: left column stacks price over "N bed"; right column
  stacks the couple and others monthly figures, each on its own row,
  right-aligned (both right edges measured at the same x).
- **Superuser truth**: the user's account (Ashby, emily.winch@gmail.com)
  was NOT a superuser — the live persons config had `is_superuser:
  false` for everyone. The headless check used a dev-minted cookie
  (simon@example.com, superuser) — not representative. Ashby is now
  `is_superuser: true` via the merge PATCH (other fields untouched);
  because the flag is baked into the session cookie at login, she must
  log out and back in for the 👤 "Switch person" button to appear.
- `public/header-check.png` re-taken from Ashby's OWN session
  (emily.winch@gmail.com → Ashby, superuser, impersonation bar open) —
  the troubleshooting screenshot now matches what the user will see.
- Commute chains regenerated live (`*/poi`, `*/primary_school`,
  `*/secondary_school`, `*/walk`, `*/drive`, transit/rail/park-and-ride
  chain) so persisted legs carry the destinations.

## Verification

- `make test` green; ruff + basedpyright clean; language sweep green.
- Two-phase walkthrough re-run as the final acceptance for any UX batch
  (participant confusions gone / downgraded).
- PR: all work lands on `feat/settings-screen` (PR #53) and goes through
  the pr-review loop until findings add little value.
