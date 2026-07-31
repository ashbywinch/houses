# Personas & User Journeys

Four people buying a house together in/near London. Fixed monthly budget. Complex requirements: multiple commutes, school logistics, disability access, neighbourhood quality.

## Personas

| Person | Role | Cares about | Usage pattern |
|---|---|---|---|
| **Ashby** | Primary user, filterer, money person, disabled. Needs annexe (existing or convertible: garage, outbuilding, separate floor with external access) | Money — within budget; verifies every calculation. Also everyone's commutes + schools | Initial screener: scrolls new properties, quick opinions, rules out, comments, digs into finances on survivors |
| **Simon** | Brother, commuter, co-decider | Everyone's commutes — pointless if anyone's doesn't work. Group discussion | Reviews Ashby's surfaced properties; checks commutes for all; thumbs up/down |
| **Lorena** | Sister-in-law, neighbourhood person, co-decider | Everyone's commutes; walkability, area feel, amenities, what it's like to live there, child's school | Reviews survivors; checks commute, area, schools; participates in discussion |
| **Child** | Dependent, school-aged | — | Good school within reasonable distance (cared about by everyone) |

## Shared concerns

### Commutes

Length AND difficulty: number of changes, long walks, particularly nasty tube lines.

### Trust in calculations

**All four are suspicious of automated calculations.** They need to see how each figure was arrived at — formula, inputs, source, timestamp. Applies especially to: monthly cost breakdown, commute times, school distances, walk times, EPC data.

**Not decorative.** Ashby would click provenance details frequently if they worked. The current provenance display is confusing and undermines trust. Getting this right is essential for tool trust.

### Group discussion

Happens via WhatsApp. Ashby flags a property, others share thoughts, debate, reach a conclusion (rule out / book viewing). The tool should **support** this discussion, not replace it.

### Per-property discussion

Property-specific conversations (annexe feasibility, cost estimates, pros/cons) get lost in WhatsApp — messages about different properties mix together. Each property needs its own easy-to-find discussion space.

### Three distinct property states

| State | Meaning |
|---|---|
| **Saved** | Actively like it, shortlisted |
| **Dismissed** | Hard no, ruled out |
| **Seen** | Physically visited — lookup aid for reviewing pictures, NOT a decision status |

Distinct needs, not interchangeable: a property can be dismissed without being seen, or seen without being dismissed.

### Filters must match the workflow

Current filters (Maybe, Undecided) don't match triage. Users need: Saved, Dismissed, Seen, unprocessed.

### Sort depends on task

Scanning new properties → most-recently-added. Reviewing survivors → overall quality (good commutes, walkability, affordability rise to top).

### Freshness helps identify new leads

Knowing when a property was added spots ones not yet considered.

### Energy efficiency is part of cost

EPC rating affects running cost → feeds the affordability decision directly.

### Finding after visiting

After a physical visit, users need to find the property again easily to review pictures/details. The "Seen" state serves this lookup need, not triage.

## User Journeys

### Journey 1: Scanning new properties (Ashby, solo)

Many new properties, most "meh." Get through quickly, rule out obvious nos, flag potentials.

Decisions per property: affordable? commutes manageable for everyone? schools acceptable? Any clear no → rule out. Particularly exciting → shortlist.

### Journey 2: Evaluating survivors (the group)

Dig deeper per property:

- **Ashby:** Can this support an annexe? How much?
- **Lorena:** What's the area like? Shops/amenities proximity?
- **Everyone:** Pictures + floorplan, share thoughts, debate, conclude.

Currently via WhatsApp back-and-forth. Conclusion: rule out or book a viewing.

### Journey 3: Deep dive into one property (any user)

Each checks their priority dimension — Ashby: cost breakdown, layout, annexe potential; Simon: costs, commute routes; Lorena: commute details, area description, schools.
