# UX Fixes Round 2 — Walkthrough Findings (Scenarios A & B)

Status: draft (2026-08-04), awaiting sign-off. Source: repeatable walkthrough
prompt [docs/ux-walkthrough-prompt.md](ux-walkthrough-prompt.md) run 2
(agent `UxWalkthrough2`). Baseline: [docs/usability-requirements.md](usability-requirements.md)
(principles P1–P6, requirements A1–D4).

## Scope

Fix the walkthrough confusions, honouring the user's decisions:

1. **Reuse the existing provenance components** — and standardise the
   "show provenance" affordance into ONE reused component (no bespoke
   breakdown widgets, no divergent triggers).
2. **Scenario B is just a toggle**: per person, "I am selling a home to fund
   this purchase". ON → current-home fields (sale price, mortgage remaining)
   apply; OFF → no current home, deposit is cash. Ashby is the exemplar of
   OFF (never sold, deposit is `cash_contribution`). No inference-from-zeros,
   no state machine, no "sold my home" conversion action.
3. **The mortgage stays** — it's Simon's mortgage on the new purchase.
   Nothing removes or zeroes it; the fix is labelling/explaining it.
4. **No work/personal distinction** — dropped. Schools are the only kind we
   distinguish, and they're not user-editable (already handled: school
   commutes render in the schools section, not the commute rows).
5. **No toolbar links** — discoverability comes from a standard
   **person/settings drop-down in the header**, as on normal websites.
6. **The £?/mo data issue (38/40 properties) is known and OUT OF SCOPE.**
   The `£?/mo` + tooltip already shipped; the root cause is a data problem.

## Findings being fixed

| # | Finding | Fix workstream |
|---|---|---|
| 1, 5 | Commute editing undiscoverable; no add/remove in the Commutes editor | W-A |
| 2 | "Pimlico" label not self-explanatory as the office | W-A |
| 3, 4, 12 | Sale-price → deposit → mortgage chain opaque | W-C |
| 7, 8, 9, 11 | No "house sold / no current home" state | W-B |
| 10 | Cash buyer sees a mortgage line, unexplained | W-B, W-C |
| 14 | Council Tax note still cryptic | W-E |
| 15 | `ⓘ how?` provenance affordance too subtle + inconsistent | W-C |
| 6 | "Dad" reads like a work commute | dropped (not a distinction we care about) |
| 13 | £?/mo prevalence | out of scope (data issue) |

## Workstreams

### W-B — "Selling a home to fund this purchase" toggle

Problem: a family that sold (or never had) a home cannot express it;
the current-home fields describe a house that doesn't exist (findings 7, 8,
9, 11).

1. **Model**: `Person.selling_home: bool | None = None` — unset means
   "infer": `effective_selling_home(person) = selling_home if not None else
   bool(home_sale_price or outstanding_mortgage)`. This is the migration:
   Simon (has home values) infers ON; Ashby (zeroed) infers OFF; once the
   user touches the toggle it's explicit.
2. **Math honours the toggle** (it's a settings input, so the DAG reads it
   like the money fields): `EquityTotalNode` counts `max(0, sale − mortgage)`
   ONLY when `effective_selling_home` is true; otherwise equity is
   `cash_contribution` alone. Ashby OFF → deposit is his £300k cash.
3. **Defaults + DB**: `make_default_persons` sets Simon ON, others OFF
   (explicit thereafter); one-off DB fix sets Simon's persisted toggle ON
   (DB only, like the email fix) so his £177k home equity keeps counting.
4. **UI (SettingsView)**: per-person checkbox "I am selling a home to fund
   this purchase". ON → "Expected sale price" / "Mortgage remaining" fields
   shown. OFF → hidden; note "Deposit is cash — no current home"; the
   deposit field relabels "Cash available for the deposit"
   (still `cash_contribution`). Ashby renders OFF by default.
5. **Costs framing (finding 10)**: the Mortgage line stays — it's Simon's
   mortgage on the new purchase. One-line note under it when equity is the
   dominant source: "The deposit covers most of the price — the remaining
   mortgage is Simon's."

Tests (red/green): `effective_selling_home` inference + explicit override;
EquityTotalNode excludes home equity when OFF (Ashby-shape: cash only);
SettingsView toggle hides/shows the fields and relabels; PATCH round-trips
`selling_home` (merge semantics); the DB fix keeps Simon ON.

### W-C — One standard "show provenance" component

Problem: the derivation chain is opaque (findings 3, 4, 12) and the current
`ⓘ how?` triggers are subtle and divergent (finding 15).

1. **Component**: a single `ProvenanceToggle.vue` — renders the trigger
   ("How is this calculated?") and toggles the existing `ProvenanceView`.
   Used EVERYWHERE provenance is shown; the `ⓘ how?` buttons in
   CostsSection are replaced with it (same data, standard trigger).
2. **Deposit provenance (server)**: `GET /api/settings` `household_deposit`
   gains a `provenance` block shaped like node provenance — per-person
   formula lines (`Simon: £550,000 sale − £373,000 mortgage + £0 =
   £177,000`; a person with the toggle OFF shows `£0 home + £300,000
   cash = £300,000`) and the total. Arithmetic already exists server-side;
   this only serialises it.
3. **SettingsView**: the deposit summary renders its breakdown through
   `ProvenanceToggle` + `ProvenanceView` — same component as the Costs page.
4. **Costs copy**: the mortgage `how?` (now `ProvenanceToggle`) gains the
   one-line hint "reduce it by raising the deposit in Settings" so the
   deposit → mortgage direction is explicit (finding 3).

Tests (red/green): GET /settings deposit provenance has correct per-person
lines incl. toggle-OFF persons; CostsSection and SettingsView both render
through `ProvenanceToggle`; component tests for the toggle behaviour.

### W-A — Commute editing: header drop-down + full CRUD

Problem: cards give no path to change destinations (findings 1, 5);
"Pimlico" isn't self-explanatory as the office (finding 2).

1. **Header person/settings drop-down** (standard pattern, replaces the
   toolbar-link idea): the header gains a menu (alongside Admin/Logout)
   listing the household people → each item navigates
   `#/settings?person=<name>` (the round-1 person-scroll param scrolls to
   that person's section). One affordance, discoverable from every screen.
2. **POI editor CRUD**: "Add destination" per person's Commutes section and
   a Remove (×) per POI row. PATCH already replaces the full POI list — no
   endpoint change.
3. **Label helper**: "Destination name" helper becomes explicit: "Shown on
   cards as 'Simon → <name>'. Edit it to your new office."

Tests (red/green): Header menu renders the people and links with the person
param; SettingsView Add inserts a blank POI row, Remove deletes it, saving
sends the updated list. No backend change.

### W-E — Council-Tax note polish

Problem: the note is cryptic (finding 14).

1. **Copy**: "Couldn't look up Council Tax — make sure the property's
   address is complete and correct (Edit address above)." Same placement,
   actionable.

Test (red/green): CostsSection renders the new wording; language sweep
stays green.

## Requirement baseline additions

- **B7**: The user can say, per household member, whether a home is being
  sold to fund the purchase; a member with no home contributes their cash
  only, and no current-home questions are asked of them.
- **B8**: When the household buys with a mortgage, the app says plainly that
  the mortgage is expected (whose it is) and how the deposit reduces it.
- **A7**: The user can add, remove, and rename commute destinations, and
  find where to do it from the header on any screen.
- ~~A8 (work vs personal)~~ — dropped per decision 4.

Existing A1–D4 unchanged.

## Out of scope (explicit)

- The £?/mo prevalence root cause (known data issue; the tooltip stays).
- Any change to the mortgage/equity arithmetic beyond the toggle gate.
- Work/personal commute distinction.

## Verification

- Red/green per workstream; unit + frontend suites; ruff + basedpyright +
  language sweep.
- Live smoke (read-only where possible): Ashby renders cash-only with the
  toggle OFF; toggling Simon ON shows his home fields; deposit provenance
  renders via the standard `ProvenanceToggle`; the header drop-down jumps to
  a person's section.
- Re-run `docs/ux-walkthrough-prompt.md` afterwards — confusions gone is the
  final acceptance.

## Execution order

W-B (toggle + equity gate) → W-C (provenance component + deposit provenance)
→ W-A (header menu + CRUD) → W-E (copy) → full suite + walkthrough re-run.
