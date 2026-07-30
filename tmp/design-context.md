# Provenance UX Redesign — Context

## What is Provenance?

The app is a property-search dashboard for house-hunting. Every data point shown to the user (price, commute time, school ratings, monthly costs, EPC rating, etc.) can have a "provenance" — a record of where that data came from and how it was calculated. This is built from a DAG (directed acyclic graph) of computation nodes.

## Current UI: ProvenanceTree.vue

A collapsible tree component that shows a hierarchical view of data sources and calculations. It has:
- Color-coded source badges (API / Calc / User / Config / Geocode / DB)
- A colored dot per node matching the source type
- Expand/collapse disclosure triangles
- "Expand all" / "Collapse all" buttons
- Formula boxes showing math
- Freshness indicators (green/amber/red dot based on age)
- External links to source websites
- Description text on some nodes
- Toggled open via "ⓘ how?" buttons in the UI (one at a time)

## Known UX Problems

1. **Deep nesting**: trees can be 4-5 levels deep. Every level requires clicking to expand.
2. **Duplicated subtrees**: when two DAG nodes share a dependency, the full subtree appears under both parents. User sees the same "best_location" → "geocode" → "postcode" chain twice.
3. **Technical labels**: internal names like "commute_breakdown", "transit_result", "mortgage_required" exposed directly — meaningless to non-technical users.
4. **One-at-a-time**: only one "how?" button can be active per section. No side-by-side comparison.
5. **Mixed concepts**: data sources (API calls, geocoding, user input) mixed with calculation steps in the same tree.
6. **No overview**: you must expand everything to understand the flow. No summary view.
7. **Formula boxes are dense**: all the math is shown but no explanation of *why* the calculation exists.
8. **No value context**: individual node values are hidden unless you open a formula box.
9. **No search or filter**: if a user wants to find a specific data source or value, they must manually traverse.

## Data Model

```typescript
interface Provenance {
  label: string          // Human-readable name
  description?: string   // Optional explanation
  url?: string           // Link to source
  sourceType?: "api" | "calc" | "user" | "config" | "geocode" | "db"
  freshness?: string     // ISO date — when data was fetched
  formula?: {
    lines: Array<{ label: string; value: string }>
    result: string
  }
  sources?: Record<string, Provenance>  // Sub-sources / dependencies
}
```

## Sample Data (used in the demo page)

Located at `tmp/provenance-demo.html` — open in browser to see current rendering.
Screenshot at: `/tmp/omp-sshots-1543f48a86d5719a.webp`

## App Context

The app is for **house hunters**:
- Users may be non-technical, non-mathematical
- They want to understand: "Where does this number come from? Can I trust it?"
- They want explanations in plain English
- Multiple people may be looking at the same property (Simon, Lorena)
- Property data comes from Rightmove, Google Maps TFL, gov.uk APIs, manual user entry

## Design Goals

- Make provenance **understandable at a glance**
- Explain data sources and calculations in **plain language**
- Show the **flow of data** (from source to displayed value) visually
- Handle **duplicated nodes** gracefully (show once, reference elsewhere)
- Let users **compare** provenance across different fields
- Work for both **simple** (single API call) and **complex** (deep chain of calculations) cases
- Be **accessible** — keyboard navigable, screen-reader friendly

## What to Deliver

1. Analysis of UX gaps for a non-technical user
2. HTML/CSS prototype of a new provenance view (standalone, self-contained file)
   - Must work without any build tools (vanilla HTML/CSS/JS)
   - Should include the same sample data as the current tree
   - Should show the design approach for at least these three scenarios:
     a. Simple: EPC rating (single API call with one dependency)
     b. Medium: Commute cost (aggregated from two similar sub-trees)
     c. Complex: Total monthly cost (deeply nested, many sources, formulas)
