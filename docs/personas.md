# Personas & User Journeys

## Overview

Four people buying a house together in/near London. Budget is fixed monthly. Requirements are complex: multiple commutes, school logistics, disability access, neighborhood quality.

---

## Personas

### Ashby

**Role:** Primary user, filterer, money person, disabled
**Needs an annexe:** Wants properties with existing annexe or space that can be converted (garage, outbuilding, separate floor with external access)
**Financial focus:** Cares most about the money side — is it within budget?
**Attitude toward the software:** Suspicious of automated calculations. Wants to see exactly how each figure was computed, not just a number.
**Usage pattern:** Initial screener — scrolls new properties, forms quick opinions, rules out unsuitable ones, adds comments, digs into finances on survivors.

### Simon

**Role:** Brother, commuter, co-decider
**Cares about:** His own commute time and cost.
**Usage pattern:** Reviews properties Ashby surfaces. Checks commute. Gives thumbs up/down. Participates in WhatsApp discussion about pros/cons.

### Lorena

**Role:** Sister-in-law, neighborhood person, co-decider
**Cares about:** Walkability, area feel, amenities, what it's like to live there. Also cares about her own commute and the child's school.
**Usage pattern:** Reviews survivors. Checks area feel and schools. Participates in WhatsApp discussion.

### Child (school-aged)

**Role:** Dependent
**Cared about by:** Everyone
**Needs:** Good school within reasonable distance.

---

## Shared Concerns

### Trust in calculations

All four are suspicious of automated calculations. They want to see how each figure was arrived at — the formula, the inputs, the source, the timestamp. This applies to: monthly cost breakdown, commute times, school distances, walk times, EPC data.

### Group discussion

The group currently discusses properties via WhatsApp. Ashby flags a property, others share their thoughts, they debate pros and cons, eventually reaching a conclusion. The tool should support this discussion, not replace it.

---

## User Journeys

### Journey 1: Scanning new properties (Ashby, solo)

Ashby opens the tool and sees newly discovered properties. There are lots of them — most are "meh." The goal is to get through them quickly, ruling out the obvious nos and flagging the potential ones.

**What Ashby needs to decide for each property:**
- Is it affordable?
- Are the commutes manageable for everyone?
- Are the schools acceptable?

If any of those is a clear no, rule it out. If all three look OK, flag it as a potential.

### Journey 2: Evaluating survivors (the group)

The group looks at properties that passed the initial triage. For each one, they need to dig deeper:

- **Ashby:** Can this property support an annexe? How much would that cost?
- **Lorena:** What's the area like? How close are shops and amenities?
- **Everyone:** Look at the pictures and floorplan. Share thoughts. Debate pros and cons. Reach a conclusion.

Currently this happens via WhatsApp messages back and forth. The conclusion is either "rule it out" or "book a viewing."

### Journey 3: Deep dive into one property (any user)

Someone wants to understand a specific property thoroughly. They check their priority dimension — Ashby looks at the cost breakdown, Simon checks his commute route, Lorena reads the area description and school details. They want the full detail, not a summary.
