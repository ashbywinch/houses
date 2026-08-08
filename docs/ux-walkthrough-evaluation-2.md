# UX Walkthrough Evaluation — Run 2 (Evaluator Report)

**Date:** 2026-08-06
**Participant:** UxParticipant2 (Phase 1)
**Evaluator:** UxEvaluator (Phase 2)
**App:** Houses (family house-hunting tool)
**Goal:** Find a house and get accurate commute + monthly-spend numbers
**Baseline:** docs/usability-requirements.md (P1–P13, Journeys A–D)

---

## Environment notes

The participant walked the **live app** whose persisted per-property DAG results
were computed **before** recent changes. The council-tax node now returns a
Band-D estimate with a spread on lookup failure (instead of '?'), and the
total monthly cost inherits that estimate — but persisted results only
recompute when stale. So "Total Monthly — Can't calculate" and card "£?/mo"
on many properties are **stale persisted state**, not the new code's behaviour.
Those findings are attributed to the data-state/refresh problem below, not to
the UI layer.

The "Commute ceiling — some houses hidden" filter and the "What if…" panel
**are** new (this session's work) — evaluated on their merits.

The participant was told **not** to type into or change any inputs and not to
press Save/Apply-like buttons — so gaps in the explore path may be
protocol-constrained, not user failure. Where this matters, it is noted.

---

## Confusions (the user's experience)

Numbered by severity against the goal (accurate commute + monthly spend).

### 1. Every house shows "Can't calculate" for Total Monthly — goal #2 completely blocked

**Severity:** Blocker

**What the user was trying to do:** Find out what the family would pay each
month for a house — the primary goal.

**Evidence:**
> "Can't calculate! That's the thing I came here to find out — what we'd pay
> each month — and it says it can't calculate it." *(Thurlby Way Costs tab)*

> "So I've now got: one house with a mortgage of £1,080 but no total, one with
> no mortgage and a £1,230 commute, and I still can't get a single 'this is
> what you'd pay per month' number out of this website. Two out of two houses
> say 'Can't calculate'." *(after Haydon Road)*

> "Third house, third 'Can't calculate'." *(Hamilton Avenue)*

Screenshots: `07-costs-tab-scrolled.webp`, `09-haydon-costs-cant-calculate.webp`,
`18-hamilton-costs-cant-calculate.webp`

**Why it confused them:** The participant opened three houses and every single
one returned "Can't calculate" for the total monthly figure. Council Tax
shows "?" on all three, which appears to block the total. The user has no
way to fix this (no address-edit UI was reached; the participant was
protocol-constrained from editing). This completely blocks the monthly-spend
goal.

**Root cause (for attribution):** Stale persisted DAG state — the council-tax
node returns '?' when lookup fails. The new code returns a Band-D estimate
instead, but persisted results have not been recomputed. This is a
data-state/refresh issue, not a UI deficiency. However, the user's
experience is the same either way: the total is blocked and the fix path is
unclear.

**Requirement affected:** C1 ("Failures say what's missing"), B5 ("The monthly
payment can be trusted"), B4 ("Money changes propagate automatically").

---

### 2. Commute destinations are all old office — no path to the new one is visible

**Severity:** Blocker

**What the user was trying to do:** Find the commute time to their *new*
office — goal #1.

**Evidence:**
> "Pimlico, Bracknell... those are my OLD office destinations. I moved offices
> recently! None of these destinations is my new office. So these commute
> times, even though they're there and look detailed, they're not answering my
> question. I need to know the commute to my NEW office and I don't see it
> anywhere on this page."

> "I still notice: my new office isn't here. I moved recently, and this commute
> is to my old office."

Screenshots: `05-property-details-thurlby-way.webp` (commute section showing
Pimlico/Bracknell/Aldgate)

**Why it confused them:** The commute section shows detailed times and costs
to Pimlico, Bracknell, and Aldgate — all old destinations. The participant
knew immediately these were wrong for their situation but had no visible
path to change them. A "Change destinations →" link exists on the detail
page, but the participant (protocol-constrained) did not click it. Even
if they had, the concept of "destinations" vs "offices" and where settings
live is not obvious from the card or detail page.

**Note:** The participant was told not to modify inputs, so this may be a
protocol-constrained gap rather than a genuine discovery failure. However,
the *absence* of any affordance on the card list (where commute times are
first seen) means even an unconstrained user would need to drill into a
detail page and notice the small "Change destinations →" link — the path
is not discoverable from the cards where the problem is first visible.

**Requirement affected:** A1 ("Commute figures are attributable"), A2 ("One
change updates every property"), A5 ("Destination lists are per-person and
legible"), D2 ("Settings are discoverable from the numbers").

---

### 3. Commute-ceiling filter defaults on, shows 0 houses, and reappears on every navigation back

**Severity:** High

**What the user was trying to do:** See houses on the front page.

**Evidence:**
> "Zero houses?! But there's this little tag saying 'Commute ceiling — some
> houses hidden' with an × next to it. So there ARE houses, they're just
> hidden because of this 'commute ceiling' thing. I don't know what a commute
> ceiling is — I never set one. That's confusing: I open the site to find
> houses and there are none, and some filter I didn't create is hiding them."

> "Oh — it's back to '0 properties found'! The 'Commute ceiling — some houses
> hidden' filter is back again even though I removed it before. So every time
> I come back to the list, the houses disappear and I have to remove this
> filter again. That's going to get old fast."

> "Third time this filter has come back on its own."

Screenshots: `01-front-page-top.webp` (initial state: 0 houses + chip),
`20-filter-reapplied-on-return.webp` (filter reappeared after viewing a house)

**Why it confused them:** Three distinct problems compound:
1. The filter defaults to ON, so the very first screen shows zero houses —
   the user's first impression is "the app is empty/broken."
2. The chip text "Commute ceiling — some houses hidden" uses jargon the
   user has never encountered; they "don't know what a commute ceiling is."
3. The filter silently re-applies every time the user navigates back from
   a detail page, forcing them to remove it repeatedly (3 times in this
   session). This creates a "Sisyphean" loop: browse house → return → 0
   houses → remove filter → browse → return → 0 houses → …

**Requirement affected:** D1 ("Affordability is visible from the list"),
D2 ("Settings are discoverable from the numbers").

---

### 4. Bare "?" for missing commutes — no explanation

**Severity:** High

**What the user was trying to do:** Understand commute times for Hamilton
Avenue.

**Evidence:**
> "Commute: 'Simon/Pimlico — ?', 'Simon/Bracknell — 33m £4.76', 'Simon/Dad —
> 1h38 £21.83', 'Lorena/Aldgate — ?', schools fine. So for THIS house it
> can't even work out two of the commutes — just question marks. So I can't
> tell how long my commute would be from here at all."

Screenshot: `19-hamilton-commute-question-marks.webp` — the "?" is a bare
glyph with no tooltip or accompanying text.

**Why it confused them:** The "?" appears inline with no explanation — is
the route missing? Is the address wrong? Is it still calculating? The
user cannot distinguish "no route found" from "very slow" from "data
missing", which is exactly what A4 requires. The visual model confirmed
the "?" has no `title` attribute or visible tooltip in this persisted state.

**Requirement affected:** A4 ("A missing commute never looks like a real
one"), C3 ("No partial value masquerades as a real one").

---

### 5. Card monthly figure "£?/mo" renders ambiguously — looks like "£7/mo"

**Severity:** Medium

**What the user was trying to do:** Glance at a card to judge monthly
affordability.

**Evidence:**
> "the monthly cost — top right of each card there's a green figure that
> reads like '£7/mo'. Seven pounds a month? For a house that's £650,000?
> That can't be right. That has to be wrong. Wait... is that a question mark
> that looks like a seven? Either way it doesn't make sense to me."

> "and on the card for this house it said '£7/mo'... or '£?/mo'... but in
> here the monthly total is 'Can't calculate'."

Screenshot: `17-monthly-cost-unknown-closeup.webp` — the vision model
confirmed the glyph is a question mark, but at screen resolution the
green "?" reads ambiguously as "7".

**Why it confused them:** The participant could not distinguish the unknown
marker from a real figure. Even if correctly read as "£?/mo", the value
"unknown" for a £650k house is uninformative. The visual ambiguity
compounds: the user either sees a wrong number (£7) or an unexplained
symbol (?), neither of which helps them judge affordability.

**Note:** The `£?/mo` rendering is the new W3 fix (showing a muted unknown
marker instead of hiding the figure entirely). The root cause is stale
persisted state (council tax '?'), but the *glyph design* is the UI
question. The font/size/resolution makes the question mark
indistinguishable from a digit.

**Requirement affected:** D1 ("Affordability is visible from the list"),
P2 ("Every number is a claim"), A4 ("A missing commute never looks like
a real one").

---

### 6. Favourites view has no heading — indistinguishable from the properties list

**Severity:** Medium

**What the user was trying to do:** See their saved houses.

**Evidence:**
> "I clicked Favourites and... it looks like the same properties page again.
> Same header, same 'What if…' panel, same layout. It says '1 properties
> found' and shows one house — 31 Isambard Road, Southall. So I think this
> IS the favourites view, but it doesn't say 'Favourites' anywhere on the
> page — I had to guess from the count that it was different."

> "Actually wait, is this really favourites? There's no obvious label. If I
> hadn't been paying attention I'd think the website was broken."

Screenshots: `12-favourites-view.webp`, `14-favourites-single-house.webp` —
the page heading reads "Properties" (not "Favourites"); the only indicator
is the bottom-nav heart icon being highlighted and the count changing from
40 to 1.

**Why it confused them:** The page heading, layout, and "What if…" panel are
identical to the properties list. The only differences are (a) the count
changes and (b) the bottom-nav heart is highlighted — but neither is
prominent. A user expecting a labelled "Your saved houses" page gets
something that looks like the same list with fewer items.

**Requirement affected:** D1 ("Affordability is visible from the list" —
implies clear view identity), D4 ("No internal concepts leak").

---

### 7. "What if…" panel dominates before any house is shown

**Severity:** Medium

**What the user was trying to do:** See houses.

**Evidence:**
> "Wait — that's a LOT of numbers before I've even seen a house. And I can
> see £550,000 there as the expected sale… And then — hmm, where are the
> houses? I'm here to look at houses."

Screenshot: `01-front-page-top.webp`, `02-front-page-scrolled.webp` — the
"What if…" panel with financial inputs (sale price, mortgage, cash,
commute days per person) fills the viewport before any property cards are
visible.

**Why it confused them:** The panel presents personal financial data
(Simon's sale price, mortgage remaining, cash available) before the user
has seen any houses. For a first-time visitor whose mental model is "I'm
here to browse houses," this is disorienting — the app leads with
configuration rather than content. The panel's instructional text
("Try different numbers…") assumes the user already understands the
financial model, which they don't.

**Requirement affected:** P4 ("Organize by the user's world"), D1
("Affordability is visible from the list" — houses should be visible
without scrolling past configuration).

---

### 8. Sort & Filter offers "Max Monthly Outgoings" when the app can't compute monthly

**Severity:** Medium

**What the user was trying to do:** Understand what filtering options exist.

**Evidence:**
> "there IS a place where you can set a maximum monthly amount. That's exactly
> the kind of thing I'd want to use — I want to know what we can afford per
> month. But I'm not touching the inputs or pressing Apply. Hmm, but here's
> the thing that's bugging me: there's a 'Max Monthly Outgoings' filter, yet
> every house card shows a monthly figure that's either a question mark or '£7'
> and the details pages say 'Can't calculate'. So the app wants me to filter
> by a monthly number, but it can't even tell me the monthly number. That
> feels backwards."

Screenshot: `13-list-with-filter-click.webp`

**Why it confused them:** The filter promises a capability (filter by monthly
outgoings) that the app cannot deliver (monthly outgoings can't be
calculated). This undermines trust: if the app can't compute the number,
why is it offering to filter by it?

**Note:** In a working state (post-recompute), this filter would be useful.
The confusion arises because the persisted state is stale. However, the UX
should handle the case where the filter value is unavailable — e.g.
greyed-out or accompanied by an explanatory note.

**Requirement affected:** P2 ("Every number is a claim"), C1 ("Failures say
what's missing").

---

### 9. Commute costs very high (£1,230/mo) with no surface explanation

**Severity:** Medium

**What the user was trying to do:** Understand what they'd pay for commuting.

**Evidence:**
> "Commute Cost — £1,230.85! Simon £464.18/mo and Lorena £766.67/mo. That's
> over twelve hundred pounds a month just on commuting. That's huge — I don't
> know why it's that high."

Screenshot: `09-haydon-costs-cant-calculate.webp`

**Why it confused them:** The figure is likely a TfL daily-cap artefact
(£100/day × ~25 days), but the user sees only the total with no
explanation of what went into it. For a figure this far outside normal
expectations, the absence of context (breakdown, cap note, or tooltip)
makes it untrustworthy. The W3 fix added a tooltip when the cost equals
the TfL cap, but the participant did not hover to discover it — and on
the detail page's Costs section, the figure appears without any such
note.

**Requirement affected:** P2 ("Every number is a claim"), B5 ("The monthly
payment can be trusted").

---

### 10. House count flickered with no visible explanation

**Severity:** Low

**What the user was trying to do:** Browse houses.

**Evidence:**
> "houses are back — 40 of them this time it says, '40 properties found'.
> Hmm, earlier it said nothing when I removed it, then '0', then '1' at one
> point — the count keeps changing and I can't tell what's real."

**Why it confused them:** Minor: the property count changed across the
session (0 → 1 → 40) with no visible trigger. The user attributes it to
the filter toggling, but the lack of a stable count undermines confidence
in the app's state.

**Requirement affected:** P2 ("Every number is a claim").

---

## UI/UX recommendations

Numbered; each tied to the confusion(s) it resolves.

### R1. Default commute-ceiling filter to OFF and persist removal across navigation

**Change:** Two-part fix:
- (a) Default the commute-ceiling filter to **OFF** on first visit so the
  user sees houses immediately. Add a one-time onboarding hint ("Commute
  ceiling filters out houses far from your office — tap to turn it on")
  if discoverability is a concern.
- (b) When the user removes the filter, persist that choice (session state
  or URL param) so navigating to a detail page and back does not
  re-enable it. The filter state should survive the same way a scroll
  position or sort choice would — it's a view preference, not a
  page-level default.

**Where:** Filter-chip component; filter state management (store /
query-param / session preference).

**Resolves:** Confusion #3 (filter defaults on, reappears on every return,
blocks browsing).

---

### R2. Make the "?" commute glyph unambiguous with a tooltip and distinct visual

**Change:** Replace the bare "?" in commute pills with either:
- (a) A tooltip (`title="No route found — check the property address"`) on
  the existing "?" glyph, or
- (b) A distinct visual treatment (e.g. "No route" label in muted text)
  that cannot be confused with a digit.

The current green "?" in the same font/size as real commute times is too
easy to misread as "7".

**Where:** `CommutePill` component (card and detail views).

**Resolves:** Confusion #4 (bare "?" no explanation), partially #5 (glyph
ambiguous with "7").

---

### R3. Add "Favourites" heading when in the favourites view

**Change:** When the user navigates to the Favourites view, show a
page-level heading "Favourites" (or "Saved Houses") instead of reusing
"Properties". The count text should also change: "1 saved house" instead
of "1 properties found".

**Where:** Favourites view component (page header + count text).

**Resolves:** Confusion #6 (Favourites indistinguishable from list).

---

### R4. Collapse or de-emphasise the "What if…" panel; lead with houses

**Change:** On first visit (or when no houses are visible), collapse the
"What if…" panel behind a toggle ("Adjust numbers ▾") so property cards
are the first thing the user sees. The panel should expand on demand, not
dominate the landing viewport.

Alternatively, move the panel below the property list, or make it a
sidebar/drawer.

**Where:** Front page layout; "What if…" panel component.

**Resolves:** Confusion #7 (panel dominates, houses not visible), partially
#3 (0 houses on landing).

---

### R5. Add a heading or visual cue to the Favourites view

**Change:** In addition to R3, add a distinct background or layout cue
(e.g. a "Saved" badge on each card, or a different header colour) so the
favourites view is visually distinct from the properties list at a glance.

**Where:** Favourites view; card rendering in favourites context.

**Resolves:** Confusion #6 (Favourites looks identical to the list).

---

### R6. Disable or annotate the Max Monthly filter when totals are unavailable

**Change:** When the monthly total is unavailable for most properties
(i.e. the data is stale or council tax is missing), either:
- (a) Grey out the "Max Monthly Outgoings" filter with a note
  ("Monthly totals unavailable — update property addresses to enable"),
  or
- (b) Hide the filter entirely and show it only when monthly totals are
  calculable.

**Where:** Sort & Filter panel; monthly-availability state check.

**Resolves:** Confusion #8 (filter offered when app can't compute the
value).

---

### R7. Explain high commute costs with a tooltip or breakdown

**Change:** On the Costs detail page, when the commute cost is unusually
high (e.g. exceeds a daily-cap threshold), add an inline note or expandable
breakdown: "Commute cost includes TfL daily maximum fares" or similar.
This should appear without requiring a hover — the participant never
hovered, and mobile/touch users can't.

**Where:** Costs section; commute-cost line item.

**Resolves:** Confusion #9 (£1,230/mo commute cost unexplained).

---

### R8. Make the "?" commute glyph visually distinct from digits

**Change:** Beyond the tooltip (R2), use a different colour or weight for
the "?" so it reads as "unknown" rather than "seven" at all resolutions.
Example: a greyed-out "?" or an em-dash with tooltip ("— No route"). The
goal is that no reasonable viewer could confuse the unknown marker with a
numeric value.

**Where:** `CommutePill` component; card monthly-cost unknown marker.

**Resolves:** Confusion #5 (£?/mo reads as £7/mo).

---

### R9. Surface the "Change destinations →" link on property cards, not just the detail page

**Change:** Add a small "Change destinations →" link (or icon) to the
commute section of property cards, so the user can discover the settings
path from the first place they see commute times — the card list — rather
than needing to open a detail page first.

**Where:** `PropertyCard` commute section.

**Resolves:** Confusion #2 (no path to new office visible from cards),
supports A5/D2.

---

### R10. Persist all filter/sort state across navigation (comprehensive)

**Change:** Apply R1's persistence logic to ALL filter/sort state, not
just the commute ceiling. The sort order, bedroom filter, and monthly
filter should all survive detail-page round-trips.

**Where:** Filter/sort state management.

**Resolves:** Confusion #3 (comprehensively), confusion #10 (count
flickering).
