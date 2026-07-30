# Provenance UX Gap Analysis

## Who Are These Users?

House hunters making a major financial decision. Simon and Lorena (or similar pairs) are evaluating properties worth hundreds of thousands of pounds. They are not data engineers, not developers, and may not be mathematically confident. Their questions when seeing a number like "£2,917/mo" are:

- **"Can I trust this?"** — Is this a real number or a guess?
- **"Where does it come from?"** — Rightmove, TFL, our own input?
- **"How fresh is it?"** — Was this data fetched today or three months ago?
- **"What does this actually include?"** — Which costs are in here?

Every one of these questions is currently difficult or impossible to answer from the existing ProvenanceTree component.

---

## Cognitive Barrier #1: Technical Labels Are Meaningless

The tree exposes internal variable names directly:

| Current Label | What a User Needs to Know |
|---|---|
| `commute_breakdown` | "How we calculated your commute costs" |
| `transit_result` | "Public transport route data from TFL" |
| `mortgage_required` | "The amount you still need to borrow" |
| `stamp_duty` | "The tax you pay when buying a property" |
| `best_location` | "The property address we used for calculations" |
| `persons_config` | "Your household details (names, contributions)" |
| `comment_status` | "Whether you own a home you're selling" |
| `rail_fare` | "Monthly train ticket cost" |
| `yearly_sinking_fund` | "Annual fund for property maintenance" |

These labels come from `label: str` in the Python Provenance dataclass and map directly to internal DAG node names. A non-technical user reading `commute_breakdown` has no idea whether this is a data source, a calculation, or an intermediate variable. The `description` field exists in the data model but is only populated on a handful of nodes.

---

## Cognitive Barrier #2: Deep Nesting Requires Many Clicks

The Total Monthly Cost tree is 5 levels deep:

```
total_monthly_cost
  └─ mortgage
      └─ mortgage_required
          └─ stamp_duty
              └─ price (rightmove_price)
              └─ status (comment_status)
          └─ total_works
          └─ total_equity
          └─ price (rightmove_price) ← DUPLICATE
```

To understand how the mortgage was calculated, the user must expand 3-4 disclosure triangles sequentially. Each level hides information behind another click. The `depth < 1` default in ProvenanceTree.vue opens only the first level — everything below is collapsed. A user who wants to understand the "£2,917/mo" result must manually expand 20+ nodes across 4-5 levels.

---

## Cognitive Barrier #3: The Duplication Problem

The DAG structure means shared dependencies appear as separate subtrees under each parent. In the sample data:

- `rightmove_price` appears under `mortgage_required → price` AND `yearly_sinking_fund → price`
- `best_location` (with its full geocode/postcode/address subtree) appears under Simon's transit, Lorena's transit, and both rail_fare nodes — **4 times** in the commute tree alone
- `financial_settings` appears under `monthly_mortgage` and `yearly_sinking_fund`
- `persons_config` appears under `total_equity`, `total_works`, `life_insurance`
- `geocode → best_address → user_entered_address / rightmove_address` chain is duplicated across EPC, Council Tax, and every commute origin

In the commute tree, the `best_location → geocode → best_address → rightmove_address` subtree appears **6 times** (once for each of Simon/lore transit and rail). The user cannot tell these are the same data — they see it as 6 separate sources, which either confuses them or causes them to distrust the data ("why is the same thing listed so many times?").

---

## Cognitive Barrier #4: No Narrative or Data Flow

A tree is a structural representation, not a story. The user doesn't think in terms of "parent nodes" and "child dependencies." They think:

> "I entered an address → it was geocoded → the commute was looked up on TFL → the fare was checked on National Rail → the monthly total was calculated."

The current tree shows the *computation graph*, not the *data story*. There is no visual indication of data flow direction, no sequence, no sense of "this happened, then this, then this." The formula boxes show arithmetic but not the narrative of what each step means.

---

## Cognitive Barrier #5: No Overview Without Full Expansion

There is no collapsed summary view. To see even the top-level value and its direct inputs, the user must have the tree at least partially expanded. The `expand all` / `collapse all` buttons are the only controls — there's no "summary mode" that shows key facts (source names, freshness, trust level) without requiring the user to drill into every branch.

---

## Cognitive Barrier #6: No Comparison Mode

The current UI only supports one "how?" button per section — opening one provenance tree at a time. If a user wants to compare why Commute Cost differs between Property A and Property B, or compare Commute Cost vs Mortgage Cost for the same property, they must remember the first tree's details while looking at the second.

---

## Cognitive Barrier #7: Formulas Lack Meaning

Formula boxes show:
```
Property Price     £550,000
Stamp Duty         £15,000
Total Works        £50,000
Total Equity       -£200,000
─────────────────
= £415,000
```

But there's no explanation of *why* these items combine this way, what "Total Equity" means, or why Stamp Duty is included in the mortgage calculation. The math is correct but the *reasoning* is absent. A non-technical user doesn't need to see the raw numbers — they need to understand the story: "We take the property price, add the tax and renovation costs, subtract what you already have, and that gives us the amount you need to borrow."

---

## What Non-Technical Users Actually Want

### 1. Trust at a Glance
Before drilling into details, users want to know: is this data trustworthy? They need:
- **Source identity**: "From Rightmove" not `rightmove_price`
- **Freshness**: "Updated yesterday" not a tiny colored dot with no label
- **Confidence**: Is this exact data (API lookup) or an estimate (calculation)?

### 2. A Plain-Language Story
"The property price came from Rightmove (updated yesterday). We added stamp duty and renovation costs, subtracted your deposit, and calculated the monthly mortgage payment at 4.5% over 25 years."

### 3. Understanding What's Included
"Your monthly cost of £2,917 includes: mortgage, property maintenance fund, life insurance, commute costs, council tax — minus any rental income."

### 4. Freshness Without Hovering
A date or "3 days ago" label visible without interaction, not a color dot that requires a tooltip.

### 5. Source Attribution
Clicking "From Rightmove" should link directly to the listing. Clicking "From TFL" should show what TFL endpoint was used.

---

## Specific Recommendations for the Redesign

### 1. Story-First Layout
Replace the tree with a top-to-bottom or left-to-right flow diagram. Group by narrative stage:
- **Inputs** (what you told us) → **Sources** (where we looked it up) → **Calculations** (how we combined it) → **Result** (the number you see)

### 2. Plain-Language Labels
Every node should have a human-readable label and a one-sentence explanation. Internal names like `commute_breakdown` should never appear. The existing `label` field should be overridden with descriptive names, and the `description` field should be populated on every node.

### 3. Shared Reference Library
Instead of duplicating subtrees, render shared nodes once in a "Data Sources" reference section. Calculation nodes that depend on shared data should show a compact reference link (e.g., "📍 Your property address") pointing to the single authoritative copy.

### 4. Trust Summary Bar
At the top of every provenance view:
- Overall trust level (All data fresh / Some data aging / Data may be outdated)
- Last updated timestamp in human-readable form
- Number of sources (3 data sources, 2 calculations)

### 5. Layered Detail
Three zoom levels:
- **Summary** (default): Just the result value + trust bar + one-sentence explanation
- **Story**: The narrative flow showing sources and how they combine
- **Full detail**: The complete math, every source, every freshness date

### 6. Formula Explanations
Replace raw formulas with annotated explanations:
- "We calculated stamp duty based on the property price of £550,000. As a first-time buyer, there was no relief available."
- Show the *meaning* of each line, not just its label and value.

### 7. Comparison Mode
Side-by-side layout for comparing two provenances. Highlight differences in sources, freshness, and calculated values. Use a table or split-panel layout.

### 8. Freshness as Text
Replace the colored dot with text: "Updated 2 days ago" or "Fetched from Rightmove on 29 Jul 2026." Reserve the colored dot as a secondary indicator next to the text.

### 9. Keyboard Navigation
All expandable sections must be keyboard accessible. Focus states should be visible. ARIA roles for the flow diagram (treeitem, group) should be applied.

### 10. Visual Data Flow
Use visual connectors (arrows, lines) or card-based layouts with clear directional flow to show how data moves from source to result. Avoid the flat indented list.
