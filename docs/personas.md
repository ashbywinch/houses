# Personas & User Journeys

## Overview

Four people buying a house together in/near London. Budget is fixed monthly. Requirements are complex: multiple commutes, school logistics, disability access, neighborhood quality. The tool helps filter many properties down to the few worth viewing.

---

## Personas

### Ashby

**Role:** Primary user, filterer, money person, disabled
**Needs an annexe:** Wants properties with existing annexe or space that can be converted (garage, outbuilding, separate floor with external access)
**Financial focus:** Cares most about the money side — is it within budget? What's the monthly cost breakdown? Can we afford it?
**Suspicious of calculations:** Wants to see exactly how each financial figure was computed. Provenance matters — not just "£2,850/mo" but "here's the breakdown: mortgage £1,200, sinking fund £150, insurance £25, commute £320, council tax £150 — total £1,845."
**Usage pattern:** Initial screener — scrolls new properties, forms quick opinions, rules out obviously unsuitable ones, adds comments, digs into finances on survivors.

### Simon

**Role:** Brother, commuter, co-decider
**Cares about:** His own commute time and cost. Needs to know "how long will it take me to get to work from here?"
**Usage pattern:** Reviews properties Ashby surfaces. Checks commute. Gives thumbs up/down.

### Lorena

**Role:** Sister-in-law, neighborhood person, co-decider
**Cares about:** Walkability, area feel, amenities, what it's like to live there. Will research the neighborhood before agreeing to a viewing.
**Also cares about:** Her own commute, the child's school logistics.
**Usage pattern:** Reviews survivors. Checks walk score and area description. Looks at schools. Gives thumbs up/down.

### Child (school-aged)

**Role:** Dependent
**Cared about by:** Everyone
**Needs:** Good school within reasonable distance. School run logistics (walk/bus/drive time).

---

## Shared Concerns

### Provenance & Trust

**All four users are suspicious of automated calculations.** They want to see exactly how each figure was arrived at. This applies to:
- Monthly cost breakdown (mortgage, sinking fund, insurance, commute, council tax)
- Commute time calculations (mode, route, departure time, data source)
- School distances and Ofsted data
- Walk time estimates
- EPC ratings

**The principle:** every computed value should be clickable or have a "ⓘ" / "why?" affordance that reveals:
- The formula or source
- The input values used
- The timestamp of the calculation
- Any fallback paths taken

### Fixed Monthly Budget

The group has a hard monthly budget. Every property's total monthly cost must be clear, and it must be obvious whether it's within budget or not. The delta vs their current situation is also important.

---

## User Journeys

### Journey 1: Daily Scan (Ashby, solo)

1. Open the tool — see newly discovered properties
2. For each property, quickly form an opinion:
   - **Rule out:** obvious no (wrong area, too expensive, no annexe potential)
   - **Maybe:** needs more investigation, leave it for now
   - **Potential:** looks promising, flag for group review
3. Add quick comments as they go ("garage could be converted", "too near motorway")
4. End of session: see the filtered list of survivors

**What makes this work:**
- Cards show just enough info for a yes/no/maybe decision
- Rule-out is one tap/clic
- Adding a comment is fast
- Survivors are clearly distinguishable from ruled-out

### Journey 2: Group Review (Ashby + Simon + Lorena together)

1. Open the survivor list — only properties not ruled out and within budget
2. Each person checks their priority:
   - Ashby checks finances and annexe potential
   - Simon checks his commute
   - Lorena checks walkability, schools, her commute
3. Discuss and decide: viewing? or rule out?
4. Book viewings for the ones that pass

**What makes this work:**
- Each person can quickly find the info they care about
- The detail page organizes everything by category
- Cost breakdown is transparent and trustable
- Everyone can see each dimension without scrolling through irrelevant info

### Journey 3: Deep Dive (any user on a specific property)

1. Open a property's detail page
2. Check the specific dimension they care about:
   - Ashby: affordability tab → cost breakdown → click each line for provenance
   - Simon: commute tab → his route → source and calculation
   - Lorena: schools tab → walk time and Ofsted → area description
3. Form a confident yes/no decision

**What makes this work:**
- Detail page has clear sections matching each persona's concerns
- Every calculated value has provenance on demand
- No information is hidden behind "trust us, it's correct"

---

## Design Principles

1. **Scan first, deep-dive later.** The list page is for rapid filtering. The detail page is for thorough evaluation. Don't mix the two.
2. **One tap to rule out.** The primary action on every card must be "yes/no/maybe."
3. **Provenance on every value.** If the software computed it, the user can see how.
4. **Each persona's priority is visible.** Finances for Ashby, commute for Simon, walkability for Lorena, school for everyone.
5. **Budget is always visible.** Every property's cost relative to budget must be obvious at a glance.
