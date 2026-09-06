# Usability Requirements — House Hunting (Commute & Affordability)

Status: baseline (2026-08-04). Source: UX walkthrough of the live app
(designer agent, read-only) + code verification. This is the **regression
baseline**: every iteration must keep these requirements true. Requirements
are stated as user outcomes and behaviours — deliberately NOT as UI
specifics (labels, buttons, placements). Implementation detail lives in
[docs/ux-fixes-plan.md](ux-fixes-plan.md).

## The general principles (P1–P13)

The requirements below are instances of durable principles (P1–P13). When the UI
changes or a new feature arrives, test against the **principles** first —
a requirement may be satisfied differently, but a violated principle is
always a regression.

**P1 — Facts in, consequences out.** The user's life facts (office address,
sale price, remaining mortgage) are the only things they ever enter.
Everything derived — commute minutes, deposit, mortgage, monthly payment —
is computed from those facts once, consistently everywhere, and updated
automatically when a fact changes. Anti-patterns: recalculate actions,
per-property re-entry, stale numbers, client-side re-derivation. The DAG is
the enforcement mechanism for this principle.

**P2 — Every number is a claim: true at face value, explainable one step away.**
A displayed figure asserts something about the world ("Simon's commute to
Pimlico is 54 minutes, and it's a real value"). There is no space — and no
need — for every number to carry its full biography on screen. Instead:

- **At a glance** (constrained by space, e.g. cards): the figure must not
  *mislead*. What it is and whose/which it refers to are legible, and a
  value that is not a real measurement (failed, capped, unknown) is never
  presented as if it were.
- **On demand**: the rest of the claim — what it depends on, how it was
  derived, why it's missing — is recoverable within one step (hover,
  expand, next screen), never buried a second click away.

Anti-patterns: bare place names with no owner, "Impossible"/"?" with no
explanation path, caps presented as actual fares, numbers that look real
but aren't.

**P3 — The system surfaces failure; the user fixes facts, not symptoms.**
Missing or wrong data is the normal state, not an edge case. The app names
what's missing in plain words and the fix operates on the user's own facts
(e.g. correct the address), never a workaround or a dead end.

**P4 — Organize by the user's world, not the system's.** The household is
the unit — our deposit, our commutes, the family — not Person records, POI
lists, DAG nodes. Internal words stay internal; the UI's structure mirrors
how the user thinks about their life.

**P5 — Change is a core journey, not an admin task.** The app's value is
that it stays right as life changes. Editing is discoverable from where its
consequences appear, and every edit has a visible, immediate consequence
(a closed feedback loop).

**P6 — The baseline is behaviour, enforced by machines where possible.**
Requirements are outcomes, not pixels. Where a behaviour is checkable
(propagation, language, addresses in links), it gets an automated test —
this is what makes the baseline durable against regressions.

**P7 — Real states are explicit, never approximated.** Every real-world
state a user can be in (owns a home to sell / doesn't, already sold / not,
has a car / not) is a first-class, explicit control or field — never
encoded as a combination of other inputs, never silently inferred.
Inference is allowed only as a one-time migration to an explicit value.
Anti-patterns: zero-values-as-meaning ("no house" = empty fields), forcing
the user to reverse-engineer a state out of unrelated inputs.

**P8 — One standard affordance per pattern.** Every recurring interaction
(revealing a derivation, editing a list, confirming a change) is served by
exactly one reused, standardised component across the app. Consistent
affordances are learnable; per-screen variants are invisible until
discovered. Anti-pattern: `ⓘ`-style triggers that differ per screen for
the same action.

**P9 — Navigate like the web.** Identity and settings navigation uses
standard web conventions (header menus, drop-downs), so a first-time
user's web knowledge transfers instead of learning an app-specific scheme.
Anti-patterns: bespoke page-level toolbar links for actions users expect
in a header menu.

**P10 — Model only distinctions that change behaviour or display.** Every
user-facing category, toggle, or field must change what the app does or
shows. Taxonomy that exists only to be edited is cost: it burdens the
editor, migrations, and the user. Anti-pattern: an editable
work/personal/school kind that changes nothing.

**P11 — No bootstrap deadlocks.** A first user must be able to make the
app useful without a pre-configured admin or identity linkage — a
self-service identity-claiming path or a guaranteed first-run flow.
Anti-pattern: an app where nobody can edit anything because no one is
linked and no admin exists to link them.

**P12 — User-entered data is never silently lost.** Updates merge into
existing records; a partial edit never resets unrelated fields; writes
from outside the app are guarded and explicit. Anti-patterns: replace
semantics on PATCH, unguarded script/REPL writes to the production
settings.

**P13 — UX work is accepted by re-walking the scenarios.** The repeatable
walkthrough prompt ([docs/ux-walkthrough-prompt.md](ux-walkthrough-prompt.md))
is a living instrument: usability changes land only when the scenario
walk-through no longer produces the confusions they targeted. Unit tests
prove mechanics; the walkthrough proves the experience.

| Principle | Requirements |
|---|---|
| P1 | A2, B4, B6 |
| P2 | A1, A3, A4, B5, C3, D1 |
| P3 | C1, C2 |
| P4 | A5, A6, B2, B3, D2, D3, D4 |
| P5 | A2, B4, B6, D2 |
| P6 | all (acceptance discipline) |
| P7 | B7* |
| P8 | B5, D3 |
| P9 | D2, A7* |
| P10 | — |
| P11 | — |
| P12 | — |
| P13 | — |

*Planned in [docs/ux-fixes-plan-2.md](ux-fixes-plan-2.md) (B7, A7).
P10–P13 are meta rules: they constrain how requirements are implemented
(no useless taxonomy, no bootstrap deadlock, no silent data loss) and how
they are accepted (re-walk the scenarios), rather than naming one
requirement each.

---

## How to use this baseline

- Before shipping any UI change, check the **principles** it could touch,
  then the requirements — a change that satisfies the same requirement
  differently is fine; a change that violates a principle is always a
  regression.
- Each requirement carries an **Acceptance** — the observable behaviour that
  proves it holds. Where automated, a test asserts it; otherwise a manual
  check. If a future change makes an Acceptance fail, that is a regression:
  fix it or get explicit sign-off to change the requirement.
- Requirements are grouped by user journey, not by screen. A single screen
  change can serve several requirements.

## The user

A non-technical member of the family who is buying a house together with
their partner and kids. They are not familiar with the app, not interested
in its internal model (equity, DAGs, provenance, caps), and their life
facts change over time (new office, new sale price). "The app" must make
the two numbers they care about — **how long the commute is** and **what
the monthly payment is** — correct with a minimum of effort and trust.

---

## Journey A — "We moved office": keeping the commute right

Jobs to be done:

> When my office moves, I want to record the new destination once, so that
> every house we look at shows my real commute.

> When I look at a house, I want to know whose commute each figure is and
> whether I can trust it, so I can decide if the house works for all of us.

### A1. Commute figures are attributable
The user can tell, for any commute figure on any property, **which family
member** it belongs to and **which destination** it is.
- Acceptance: list and detail views show the family member and the
  destination with every commute figure.

### A2. One change updates every property
When a family member's destination changes, recording the new destination
**once** updates that member's commute on **every property** — no
per-property editing, no manual refresh.
- Acceptance: changing a destination in settings changes the commute
  figures across the whole property list automatically (automated test).

### A3. Commute figures are verifiable against the real world
The user can check a commute against reality — directions to the
destination's **actual address**, not a vague area name.
- Acceptance: the directions action resolves to the destination's full
  address; a test asserts the address (not the label) is used.

### A4. A missing commute never looks like a real one
When a commute cannot be worked out, the app says so in plain language and
distinguishes "no route found" from "very slow". A failed figure is never
presented as a number that looks like a real time or cost.
- Acceptance: properties with no route show an explicit "can't calculate"
  state with an explanation; automated tests cover the rendering of that
  state and its tooltip/explanation.

### A5. Destination lists are per-person and legible
The user can see which destinations each family member has, and which one
needs changing — without knowing that commutes live in "settings" or which
internal names the app uses.
- Acceptance: the destination list is visibly grouped by family member and
  reachable from where the commute figures appear.

### A6. Name vs address is not a puzzle
The two inputs that define a destination — what it's **called** and where
it **is** — are distinguishable, and the user knows which one affects the
calculated commute.
- Acceptance: the two fields are labelled/explained such that a first-time
  user can say which one changes the numbers.

---

## Journey B — "We're selling for more": keeping the monthly payment right

Jobs to be done:

> When we sell our current home for more than we thought, I want to enter
> the new figures once and see our monthly payment drop everywhere, so that
> we know what we can afford.

> When I type a money number, I want to be sure it means what I think it
> means, so that the monthly payment I see is trustworthy.

### B1. Sale price and remaining mortgage are unambiguous
The user can enter what they expect to **receive for the current home** and
what they still **owe on it**, and the labels cannot be mistaken for a
market valuation, the price of the house being bought, or the mortgage on
the new house.
- Acceptance: each money input is described with the "current home" framing
  and its purpose; a first-time user can correctly state what each field
  does.

### B2. The family deposit is one number
The user sees the household's **total deposit** (sale proceeds minus what's
owed, plus any extra money) as a single figure, even though the inputs are
spread across family members.
- Acceptance: settings shows a household deposit total and its per-person
  parts; the total equals the sum of the parts (automated test).

### B3. Every money input is self-explanatory
Each money field (sale proceeds, remaining mortgage, extra deposit money)
is understandable on its own — what it's for and when it applies — without
knowing the app's internal model (equity, contributions, DAG sources).
- Acceptance: no money field relies on an unexplained internal concept;
  helper text explains each in plain language.

### B4. Money changes propagate automatically and visibly
After any money input changes, the monthly payment updates **automatically
everywhere** (property list and every property page) — there is no
"recalculate" action, no stale number the user must manually refresh, and
the change is visible without leaving the page.
- Acceptance: an automated test changes a settings money value and asserts
  the derived totals (deposit → mortgage → monthly payment) move by the
  expected amount across the list and detail views.

### B5. The monthly payment can be trusted
The user can see **what went into** the monthly payment — asking price,
deposit, works, mortgage — in a legible breakdown, without reading formulas
or understanding the app's calculation machinery.
- Acceptance: a plain-language breakdown of the monthly payment is
  available from the property page.

### B6. The user knows their change took effect
After saving a change, the user can confirm the app accepted it and see the
consequence immediately (the number that changed because of their input).
- Acceptance: saving shows confirmation; the affected figure updates
  without further navigation.

---

## Journey C — "Why can't this one be calculated?": blocked properties

Jobs to be done:

> When a house shows no monthly payment, I want to know what's missing and
> fix it myself, so that no house is a black box.

### C1. Failures say what's missing
When a property's monthly payment cannot be calculated, the app states
**which piece is missing** (e.g. council tax) in plain language — never a
bare "Impossible" or a blank.
- Acceptance: the blocked state names the missing input; automated tests
  cover the wording of the blocked state.

### C2. The user can resolve the blockage in-app
The user can fix the underlying data problem themselves. In practice the
blocker is an imprecise property address preventing the council tax lookup,
so the user can **correct the address** on the property and the calculation
retries automatically.
- Acceptance: the property page lets the user edit the address; saving it
  updates the council tax and monthly payment without further action
  (automated test for the edit → recompute chain).

### C3. No partial value masquerades as a real one
Anything not yet calculated (council tax, costs, totals) is visibly
"unknown" — never a `?` or placeholder that could be mistaken for a real
figure, and never a cap value presented as an actual cost.
- Acceptance: unknown values carry an explicit "unknown/can't calculate"
  treatment; the maximum-fare case is labelled as a maximum (tested).

---

## Journey D — First-time orientation: trust without explanation

Jobs to be done:

> When I open the app for the first time, I want to understand the numbers
> on the screen and how to change the things that matter, so that I don't
> give up or guess.

### D1. Affordability is visible from the list
The user can judge a property's affordability from the property list alone,
without opening each property.
- (Amended 2026-09-06, user-approved: the list's monthly figure is **the
  change vs the current home** — the comparison the scanning decision
  actually turns on. The absolute total remains one step away: on the
  detail Costs page and in the legend's baseline figures.)
- Acceptance: list cards show price and monthly **change vs home**
  whenever they can be calculated; when they can't, C1 applies (dash +
  reason).

### D2. Settings are discoverable from the numbers
From any place a commute or money number appears, the user can find where
to change it — the path to settings is discoverable, not a hidden header
link.
- Acceptance: the property page and list provide an obvious route to the
  relevant settings; a first-time user can find "where my office is set".

### D3. Developer artifacts are never shown unexplained
The app never presents developer/status artifacts (cryptic buttons, status
codes, "Impossible", internal mode names, unexplained caps) without a
plain-language explanation.
- Acceptance: every user-visible status/artifact carries a plain-language
  meaning; a sweep test enforces the user-language baseline
  (no isochrone/transit/shed in UI text).

### D4. No internal concepts leak
The user is never required to understand the app's internal model (equity,
DAG, provenance, acceptable modes, thresholds) to complete their task;
internal words may appear only where they also carry a plain-language
explanation.
- Acceptance: user journeys A–C can be completed without the user
  encountering unexplained internal vocabulary.

---

## Traceability (walkthrough findings → requirements)

| Walkthrough finding (see plan §Findings) | Requirements |
|---|---|
| 1 — no affordability signal on cards | D1, C1 |
| 2 — bare place names on commute rows | A1, A5 |
| 3 — nothing links to settings | D2, A5 |
| 4 — misleading money labels | B1, B3 |
| 5 — equity split across people, no total | B2 |
| 6 — no visible effect after settings change | B4, B6 |
| 7 — maps links use place label | A3 |
| 8 — "Impossible" unexplained | C1 |
| 9 — council tax fix path missing | C2, C1 |
| 10 — Place vs Address confusing | A6 |
| 11 — £100 cap looks like a fare | C3 |
| 12 — `?` commute times unexplained | A4, C3 |
| 13 — "SU" cryptic | D3 |
| 14 — triage icons unlabeled | D3 |
| 15 — `?` transit times on cards | A4, C3 |

## Related

- Implementation plan: [docs/ux-fixes-plan.md](ux-fixes-plan.md)
- Isochrone website plan (Phase 2+ must respect this baseline):
  [docs/website-isochrone-integration.md](website-isochrone-integration.md)
