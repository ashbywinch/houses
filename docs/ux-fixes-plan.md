# UX Fixes — Commute & Monthly-Payment Correctness

Status: implemented (2026-08-04), PR open. Based on the UX walkthrough
(designer agent, read-only, `history://UxWalkthrough`) of the live app
+ code verification. All workstreams below landed; requirements baseline in
[docs/usability-requirements.md](usability-requirements.md).

**The user needs this work serves are recorded as a regression baseline in
[docs/usability-requirements.md](usability-requirements.md) (journeys
A–D, requirements A1–D4). Every workstream below lists the requirements it
satisfies; an implementation must keep all of them true.**

## Goal

A non-technical user must be able to achieve, alone, two things:

1. **Correct commute distance** after moving office — find where destinations
   are changed, understand whose commute each card row is, and trust the
   number (including the directions link).
2. **Correct monthly payment** after selling the current house for more —
   enter the expected sale price / remaining mortgage, see the household
   deposit as one number, and watch the monthly payment update everywhere
   automatically.

## Principles (from the user)

- **Updates are a DAG problem, not a UI problem.** If a settings change
  doesn't propagate to per-property totals, fix the DAG/scheduler path so it
  does — never a client-side recompute or preview. The DAG exists so updates
  are automatic for everything.
- **Cards are small and cluttered.** Every card addition must be justified,
  tiny, and not add a row where avoidable. Prefer tooltips/titles over new
  elements.
- **Council tax is fixed by correcting the property's address**, not by a
  settings field. The UI must say so and provide the edit.

## Findings (walkthrough, corrected by code check)

| # | Finding | Verified? |
|---|---|---|
| 1 | Cards show no affordability signal | **Partly wrong** — price and `£X/mo` exist on cards, but `£X/mo` is *hidden* whenever the total is "Impossible" (common: council tax `?`), so many cards show nothing |
| 2 | Commute rows show bare place names (`Pimlico`), no person, no legend | Confirmed — the "person" span renders the *place label*; the person name is in the payload (`person.name`, key `Simon/Pimlico`) |
| 3 | Nothing links cards → settings | Confirmed — no affordance anywhere on cards or detail page |
| 4 | Money labels misleading: "Current home value", "Outstanding mortgage", "Cash contribution" | Confirmed (labels in `SettingsView.vue`) |
| 5 | Equity inputs split across 4 person sections, no household total | Confirmed — total equity = Σ(home − mortgage + cash) across persons |
| 6 | No visible effect after a settings change | Partly UX, partly real: DAG + WS broadcaster exist; must be *proven* end-to-end (see W2) |
| 7 | Google Maps links use place label, not address | Confirmed — `PropertyCard.vue:173` encodes `commuteLabel`; the full address is in the payload (`destination.address`) |
| 8 | "Total Monthly: Impossible" unexplained (council tax `?`) | Confirmed — `CostsSection.vue` renders bare "Impossible" |
| 9 | Council tax `?` has no fix path in the UI | Confirmed + **worse**: `patchAddress`/`patchLocation` exist in `api.ts` but are used by NO view — the address edit UI doesn't exist at all |
| 10 | Place vs Address confusing in settings POI editor | Confirmed |
| 11 | `£100.00` daily commute cost looks like a real fare | Confirmed — it's the **TfL daily cap** (operator `TfL`, cost 100.00) |
| 12 | `?` commute times unexplained | Confirmed — `CommutePill` shows `?` when duration missing |
| 13 | "SU" button cryptic | Confirmed (superuser-only but confusing) |
| 14 | Triage icons (heart/X/check) unlabeled | Confirmed |
| 15 | `?` transit times on cards | Same as #12 |

## Findings from execution

- **Propagation was genuinely broken in production** (B4): a permanently
  pending input node — `comment_status` (an empty sheet "Status" cell never
  seeded a value, and the DB-load path had no input defaults) — made
  `EquityTotalNode`'s refresh bail on `any(dep.pending)` forever, freezing
  the whole equity → mortgage → monthly-payment cascade. Fixed with
  `_seed_input_defaults` (both load paths) + a regression test; verified
  live: a £1,000 settings change moved `total_equity` 477,000 → 478,000
  within 2s via the background processor. The DAG's pending-dep wait is
  correct; the bug was the missing input, not the wait.
- **Tests must be deterministic** (see coding-standards.md): one
  `flush_all()` drains the whole cascade; compare unwrapped Money values
  (Decimal), never Attempt wrappers (provenance timestamps differ per
  read); a test that "needs two flushes" has a bug. The works-estimate
  test's old `!=` on wrapper dicts was vacuous — strengthened.
- **`/api/debug/scheduler` was an infinite loop** (pop → re-put before the
  empty check) that wedged the server on every call — rewritten as a
  read-only snapshot of `_scheduled`.

## Workstreams

### W1 — Settings money fields: renames, helper text, household deposit

- `SettingsView.vue` labels:
  - "Current home value (£)" → **"Expected sale price of current home (£)"**,
    helper: "What you expect to get when you sell it."
  - "Outstanding mortgage (£)" → **"Mortgage remaining on current home (£)"**,
    helper: "What you still owe on the house you're selling."
  - "Cash contribution (£)" → **"Other money toward the deposit (£)"**,
    helper: "Savings or gifts, on top of the sale proceeds."
- Add a **household deposit summary** at the top of the settings page, above
  the person sections: per person `home_sale_price − outstanding_mortgage +
  cash_contribution`, then the household total ("Total deposit from everyone:
  £X"). Computed **server-side** in `GET /api/settings` (new field
  `household_deposit`), not in the client — same rule as DAG-derived values.
  Shows as read-only text on the page.
- Tests (red/green first): `GET /settings` returns the correct per-person and
  household totals (unit); `SettingsView` renders the summary and the new
  labels (component).

### W2 — DAG-correct propagation of settings changes (the "updates" principle)

Goal: prove and, if needed, fix that a settings change flows to every
property's totals automatically — no client-side computation.

1. **Regression test first**: seed one property, `PATCH /settings/person`
   (e.g. Ashby cash contribution up by £1k), drain the scheduler, then
   `GET /properties/all` + `GET /properties/{rid}/detail` — assert
   `total_monthly_cost` / `mortgage_required` moved by the expected amount.
   (The existing `test_works_estimate_propagates_to_detail` is the pattern.)
2. If the test is green, the DAG already propagates; the remaining work is
   *when* the user sees it: the background processor + WS broadcaster already
   push fresh summaries to open pages — verify and document, no client code.
3. If the test is red, fix at the DAG level: trace the stale chain
   (`STALE1: …/total_equity dep=persons` warnings already prove staleness
   detection fires) — likely a node missing the `persons` dependency or a
   push not scheduling dependents. Fix the dependency wiring, keep the test.
4. Acceptance: changing any settings field (money, modes, trips, thresholds)
   updates the property list and detail totals without a manual refresh —
   covered by the regression test + live smoke.

### W3 — Card changes (declutter budget)

Only four, all small:

1. **Person name on commute rows**: the existing row label span becomes
   `Simon → Pimlico` (person from `value.person.name`, arrow, place label).
   No new elements, same single line.
2. **Monthly cost when impossible**: currently hidden. Show a muted
   `£?/mo` with `title="Can't calculate yet — see property page (Council Tax)"`.
   One span, tooltip, no layout shift.
3. **Directions link uses the real address**: `maps/dir/…/<destination.address>`
   (from `value.destination.address`), falling back to the label. Fixes #7.
4. **Triage buttons + cost tooltips**: `title` attributes on heart/X/check
   ("Favourite"/"Dismiss"/"Viewed") and on the commute cost when it equals the
   TfL cap (`title="£100.00 is the TfL daily maximum, not the actual fare"`).
   Zero new visible elements.
- Tests: PropertyCard component tests — person prefix renders, maps href
  contains the address, `£?/mo` shows when total impossible, tooltips present.

### W4 — "Impossible" and Council Tax: actionable, address-first

Per the user's directive, the council tax fix path is **the property's exact
address**, so:

1. **Address-edit affordance on the property detail page** (the missing UI):
   next to the address heading, an "Edit address" action → inline input →
   `PATCH /properties/{rid}/address` (endpoint exists) → the DAG recomputes
   council tax and everything downstream automatically. Shows the source
   address (`rightmove_address`) vs corrected, like the backend already
   models.
2. **Council Tax `?` note**: when `council_tax` is missing, the Costs row gets
   an inline note: "Couldn't look up Council Tax — the address above may not
   be exact. Edit the address to retry." (Links to the new edit.)
3. **"Impossible" totals**: replace bare "Impossible" with
   "Can't calculate — Council Tax unknown" (or the specific missing
   component, whichever node is impossible), same placement, no new rows.
- Tests: detail-page component tests — address edit appears, PATCH called,
  council-tax note renders when `?`; CostsSection renders the new copy.

### W5 — Navigation affordances

1. On the property detail **COMMUTE section header**: a small
   "Change destinations →" link to `#/settings?person=<name>`; the settings
   page scrolls to that person's section (query param read client-side).
2. **Place/Address labels** in the settings POI editor: "Place" →
   "Destination name" (helper: "Shown on property cards"), "Address" →
   "Office / location address" (helper: "Used to calculate the commute").
3. Header: "SU" → clearer label (e.g. "Admin") with the existing tooltip.
- Tests: SettingsView renders the new labels/helpers; link present on detail.

### W6 — Commute `?` and pill clarity

- `CommutePill`: when duration is missing, `?` gets
  `title="No route found for this commute"`.
- No other pill changes.

## Deferred / out of scope

- The commute summary embeds the full `Person` snapshot (payload bloat) —
  noted, separate cleanup, not UX.
- The "worst commute < X" filter and weekly-commute sort (isochrone plan
  Phase 1 items 4–5) — separate feature.
- Replacing "TfL daily max" with per-leg fare detail — data problem, not UI.

## Verification

- Red/green TDD per workstream; all tests deterministic (no network).
- `make test` green end-to-end: unit + integration + frontend + ruff +
  basedpyright + build + stylelint.
- User-language sweep (`tests/unit/test_user_language.py`) stays green — all
  new copy avoids isochrone/transit/shed.
- Live smoke (browser, no saves): change Ashby's cash contribution, watch the
  property list monthly costs update via WS; edit a property address and watch
  council tax fill in; verify the maps link opens the office address.

## Execution order

1. W2 (prove DAG propagation — decides whether UI work assumes working
   updates) → 2. W1 (money labels + deposit) → 3. W3 (cards) → 4. W4
   (address edit + Impossible) → 5. W5 → 6. W6 → verification + smoke.
