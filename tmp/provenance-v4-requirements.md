# Provenance Redesign — Requirements

## What Went Wrong Before (3 failed attempts)

**Attempt 1 (story-flow cards):** Designer replaced the tree with a linear card flow. Dropped most nodes — showed the mortgage result but not HOW it was calculated. Used aspirational text that doesn't exist in the data. User couldn't trace £2,917 → mortgage → interest rate + term. The entire point of provenance (traceability) was destroyed.

**Attempt 2 (cost breakdown + badges):** Added a horizontal cost bar and confidence badges. Same data-dropping problem. The cost bar didn't help trace the calculation — it just showed proportions. Didn't improve rendering of what's actually there. Added visual polish on top of the same missing information.

**Attempt 3 (flat list of all nodes):** Preserved every node but lost all structure. Just a pile of labeled items. No indication of what connects to what. Same problem as the old tree, just differently bad. User cannot tell what's an input vs a calculation vs a result.

**Root cause across all three:** Every designer treated "show every node" and "show how they connect" as a trade-off rather than requirements that must BOTH be met.

---

## User Requirements

### R1: A stranger must be able to explain the FULL calculation chain
Show the prototype to someone who has never seen the app, knows nothing about property, and is not mathematically confident. They must be able to point at "£2,917/mo" and — after exploring the interface — explain the entire chain:

"That's the mortgage plus the commute plus the council tax plus... the mortgage is £2,305/month, which comes from borrowing £415,000 at 4.95% over 27 years, and the £415,000 is the £800,000 property price plus £27,500 stamp duty minus £477,000 equity from selling their current home..."

Not everything needs to be visible at once — strategic hiding is fine. But every intermediate value must be discoverable within one tap/click from the thing it feeds into. If a value exists anywhere in the chain but a naive user cannot find it, the design fails.

### R2: Every value is traceable to its origin
The user must be able to follow any displayed value back through every intermediate step to its original source. The chain must be complete — no gaps where "this was calculated" but the inputs to that calculation are hidden.

### R3: The structure of the calculation is visible
The user must be able to see which things are:
- Inputs (user-entered: Rightmove price, renovation estimates, financial settings)
- API lookups (TfL, National Rail, Council Tax band, EPC register)
- Calculations (mortgage payment derived from price + rate + term)
- Intermediate values (mortgage required = price + stamp duty + works − equity)
- Results (total monthly cost)

And how they connect. Not told in a paragraph — shown visually. The relationship between nodes must be apparent.

### R4: Errors and gaps are visible and locatable
When a calculation fails (e.g. council tax lookup from ambiguous address), the user must be able to see:
- That it failed
- Where in the chain it failed
- What data was missing or what went wrong

When data is simply absent (commute cost has no value, no formula), that absence must be visible as a gap, not silently hidden.

### R5: Technical terms do not appear unexplained
If the user sees "stamp duty," "sinking fund," "equity," "mortgage term," "interest rate," or any other domain term, they must either already understand it or be able to get an explanation from within the display. No assumed domain knowledge. This can be inline, on hover, on tap, or in a glossary — but it must be there.

### R6: Freshness is per-input
The user must be able to see, for each individual source, how recently it was fetched or entered. Not one global badge over everything. A Rightmove price entered yesterday, an EPC rating from 8 days ago, and a commute cost from today must each show their own age.

### R7: Works at phone width AND desktop
Same information, same comprehensibility, at 400px wide (phone portrait) and 1400px wide (desktop). The design must be responsive to viewport width.

### R8: Aspirational — invent what's missing
The current data model is missing critical information. The prototype MUST show text, labels, explanations, annotations, and fields that don't exist yet. Every place where the current data is insufficient, invent what WOULD be needed. Annotate each invented element with "✨ Would require backend field: [field_name]".

The goal is to discover what's missing, not to constrain to what's there. If you can't make the calculation understandable with the current data, design what data you WOULD need.

---

## Data You Have to Work With

You will receive:
1. A real complex provenance JSON for Total Monthly Cost (24 nodes, including errors, duplicate nodes, empty labels, config blobs, missing values, dead-end leaves)
2. A real complex provenance JSON for a calculation with an ERROR (council tax lookup from ambiguous address)
3. A fake complex provenance JSON for a commute calculation with a 409 error deep in the chain
4. A simpler provenance JSON for EPC rating (5 nodes, clean data)

Your prototype must handle ALL of these with ONE generic component.

---

## Process

1. Read the requirements and data
2. Build an HTML/CSS/JS prototype
3. Take a screenshot **as if viewed on a phone** (400px viewport)
4. Assess the screenshot against every requirement
5. If any requirement is not met, iterate: fix the prototype, re-screenshot, re-assess
6. When ALL requirements are met, write a brief explanation of why you believe each requirement is satisfied
