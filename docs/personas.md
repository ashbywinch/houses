# Personas & User Journeys

## Overview

Four people buying a house together in/near London. Budget is fixed monthly. Requirements are complex: multiple commutes, school logistics, disability access, neighborhood quality.

---

## Personas

### Ashby

**Role:** Primary user, filterer, money person, disabled
**Needs an annexe:** Wants properties with existing annexe or space that can be converted (garage, outbuilding, separate floor with external access)
**Financial focus:** Cares most about the money side — is it within budget? Wants to verify every calculation.
**Also cares about:** Everyone's commute situation and school quality — all criteria matter to all decision-makers.
**Attitude toward the software:** Suspicious of automated calculations. Needs to see exactly how each figure was computed. Current provenance display is confusing and undermines trust.
**Usage pattern:** Initial screener — scrolls new properties, forms quick opinions, rules out unsuitable ones, adds comments, digs into finances on survivors.

### Simon

**Role:** Brother, commuter, co-decider
**Cares about:** Everyone's commute situation — no point in a property where anyone's commute doesn't work. Also participates in group discussion about pros and cons.
**Usage pattern:** Reviews properties Ashby surfaces. Checks commute for everyone, not just himself. Gives thumbs up/down.

### Lorena

**Role:** Sister-in-law, neighborhood person, co-decider
**Cares about:** Everyone's commute situation. Also walkability, area feel, amenities, what it's like to live there, and the child's school.
**Usage pattern:** Reviews survivors. Checks commute, area feel, and schools. Participates in group discussion.

### Child (school-aged)

**Role:** Dependent
**Cared about by:** Everyone
**Needs:** Good school within reasonable distance.

## Shared Concerns

### Commutes

Commuters all care about the length of their commute and also the difficulty (does it have lots of changes, or long walks, or use a particularly nasty tube line?)

### Trust in calculations

All four are suspicious of automated calculations. They need to see how each figure was arrived at — the formula, the inputs, the source, the timestamp. This applies particularly to monthly cost breakdown, commute times, school distances, walk times, EPC data.

**This is not decorative.** Ashby would click provenance details frequently if they worked. The current provenance display is confusing and doesn't serve this need. Getting this right is essential for the tool to be trusted.
### Group discussion

The group currently discusses properties via WhatsApp. Ashby flags a property, others share their thoughts, they debate pros and cons, eventually reaching a conclusion. The tool should support this discussion, not replace it.

### Per-property discussion

Property-specific conversations (annexe feasibility, cost estimates, pros and cons) get lost in WhatsApp because messages about different properties are mixed together. Each property needs its own discussion space that's easy to find later.

### Three distinct property states

Properties fall into three categories with different meanings:
- **Saved:** actively like it, shortlisted
- **Dismissed:** hard no, ruled out
- **Marked as seen:** physically visited — useful for finding the property again to look at pictures, not a decision status

These are distinct needs, not interchangeable. A property can be dismissed without being seen, or seen without being dismissed.


### Filters must match the workflow

The current filters (Maybe, Undecided) don't match how they triage. The user needs to filter by the states that actually mean something: Saved, Dismissed, Seen, and unprocessed.

### Sort needs depend on the task

When scanning new properties, most-recently-added order is fine. When reviewing survivors, the user needs to sort by overall quality — properties with good commutes, good walkability, and good affordability should rise to the top.

### Freshness helps identify new leads

Knowing when a property was added helps the user spot ones they haven't considered yet.

### Energy efficiency is part of cost

EPC rating affects the running cost of the property, which feeds directly into the affordability decision.

### Property finding after visiting

After physically visiting a property, the user needs to find it again easily to review pictures and details. The "Seen" state serves this lookup need, not a triage purpose.

## User Journeys

### Journey 1: Scanning new properties (Ashby, solo)

Ashby opens the tool and sees newly discovered properties. There are lots of them — most are "meh." The goal is to get through them quickly, ruling out the obvious nos and flagging the potential ones.

**What Ashby needs to decide for each property:**
- Is it affordable?
- Are the commutes manageable for everyone?
- Are the schools acceptable?

If any of those is a clear no, rule it out. If a property looks particularly exciting, shortlist it.

### Journey 2: Evaluating survivors (the group)

The group looks at properties that passed the initial triage. For each one, they need to dig deeper:

- **Ashby:** Can this property support an annexe? How much would that cost?
- **Lorena:** What's the area like? How close are shops and amenities?
- **Everyone:** Look at the pictures and floorplan. Share thoughts. Debate pros and cons. Reach a conclusion.

Currently this happens via WhatsApp messages back and forth. The conclusion is either "rule it out" or "book a viewing."

### Journey 3: Deep dive into one property (any user)

Someone wants to understand a specific property thoroughly. They check their priority dimension — Ashby looks at the cost breakdown and property layout / potential annexe costs, Simon looks at costs and commute routes, Lorena reads commute details as well as the area description and school details. 
