# ProvenanceView — Second Pass: Design Review & Enhancement

## What's Changed

The old `ProvenanceTree.vue` has been replaced by `ProvenanceView.vue` — a card-based layout with three detail levels (Summary / Story / Full Detail), a trust bar, plain-language labels, shared-ref deduplication, and a legend. The component is deployed and the user can see it in the app.

## What We Need From You

This is a **design review and enhancement** pass. The component exists and works. Now we need you to:

1. **Critique the current layout** — what works, what still doesn't, what gaps remain for a non-technical user
2. **Propose specific improvements** to the component or the underlying data model
3. **Build a refined HTML/CSS prototype** showing what the NEXT iteration should look like

## Key Constraint: The Backend Data Model

The component renders from this provenance JSON structure (this is ALL the data available):

```typescript
interface Provenance {
  label: string           // Internal name like "total_monthly_cost"
  description?: string    // Often empty or has error messages
  url?: string            // Link to data source
  sourceType?: "api" | "calc" | "user" | "config" | "geocode" | "db"
  freshness?: string      // ISO date
  value?: any             // The actual value from this node
  formula?: {
    lines: Array<{ label: string; value: string }>
    result: string
  }
  sources?: Record<string, Provenance>  // Recursive sub-sources
}
```

**This is NOT enough for a great UX.** We need to decide what additional fields to add.

## Things We CAN Change (on the table)

### Backend model changes (Python `dag/attempt.py:Provenance`):
- Add new fields to the dataclass (serialized in `to_dict()`)
- Add `confidence` / `trust_score` fields
- Add `category` field (group nodes into "input", "api_lookup", "calculation", "result")
- Add `plain_text_explanation` field — a sentence like "We multiplied the property price by the stamp duty rate to calculate the tax"
- Add `stage` or `step_number` for ordering in the flow
- Anything else you design

### Backend node changes (each `build_provenance()` method):
- Every DAG node can provide rich descriptions, explanations, and context
- Currently most `build_provenance()` returns just a label and sourceType
- We can add descriptions, formulas, explanations per-node

### Frontend component changes (no backend changes needed):
- Better use of existing fields (better humanLabel mapping, smarter summaries)
- Different layout, animation, interaction patterns
- More accessible markup

## What to Critique & Redesign

The current design has these elements:
1. **Trust bar** — value, freshness indicator, source count, plain-text explanation
2. **Three-level toggle** — Summary / "How we got this" / Full Detail
3. **Summary** — narrative text + source chips
4. **Story flow** — cards with arrows, grouped by stage (inputs → lookups → calculations → result)
5. **Full detail** — flattened deduplicated tree with indent levels
6. **Formula box** — step-by-step with numbered lines
7. **Legend** — source type colors

### Known remaining gaps:
- Summary text is generic ("A calculation based on the data above")
- Formula steps show math but not MEANING ("We added the property price to the stamp duty because...")
- No visual differentiation between "this is exact" vs "this is an estimate"
- The story flow is still somewhat technical
- No way to compare two provenances side by side
- No overall "trust score" — just individual freshness dots
- Labels are mapped via a static dictionary, not dynamic

## Reference Files

- `houses/frontend/src/components/ProvenanceView.vue` — the current component
- `tmp/provenanceview-demo.html` — standalone page with the new layout + JSON for each scenario
- `tmp/ux-gap-analysis.md` — previous UX analysis done by our last designer
- `houses/frontend/src/components/ProvenanceTree.vue` — the OLD component (for reference on what was replaced)

## Deliverable

Write to `tmp/provenanceview-refined.html`:

A self-contained HTML/CSS/JS prototype showing the **next iteration** of the provenance view. It should:
- Use the same three scenarios (EPC, Commute, Total Cost)
- Show BOTH the current layout AND your proposed improvements side by side or with a toggle
- If you're adding new backend fields, show them in the prototype with annotations like "✨ NEW: would come from backend"
- Include annotations explaining what changes (frontend vs backend vs both)
- Keep the same CSS variables and visual language
- Be self-contained (no external dependencies)

Also write to `tmp/provenanceview-improvements.md` a short document listing:
- Every specific change you recommend (frontend-only vs backend model change)
- Priority (P0 = must have, P1 = should have, P2 = nice to have)
- Implementation effort estimate (hours/days)
