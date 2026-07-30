# ProvenanceView — Improvement Recommendations

## Design Critique: What's Still Broken for Non-Technical Users

The current component is a solid step up from the old ProvenanceTree. The trust bar, three-level toggle, source chips, and shared reference library address the worst UX sins. But several cognitive barriers remain:

1. **The summary view is a shrug.** For the Total Monthly Cost scenario (£2,917/mo), the user's immediate question is "what makes up that number?" — and the summary gives them a paragraph and five source chips. There's no component cost breakdown, no relative weighting, no visual decomposition.

2. **Formulas show math, not meaning.** The step-by-step formula box shows `Mortgage Required: £415,000 → Interest Rate: 4.5% → Term: 25 years → £2,305/mo` but never says *why* these factors matter or what assumptions they carry. A non-technical user doesn't know what a "25-year term" implies vs a 30-year term.

3. **No confidence or certainty layer.** The EPC rating is a government-registered fact. The commute cost is a calculation based on API lookups plus assumptions about travel times. The "total works" estimate is pure user guesswork. The UI treats them all identically — same cards, same weight, no signal about which numbers are rock-solid vs which are soft.

4. **Freshness is one global bucket.** The trust bar picks the oldest date across the entire tree and stamps it on the whole view. But in the Total Cost scenario, the EPC could be 8 days old, the commute data fresh today, and the property price entered yesterday — users can't see this granularity without switching to Full Detail.

5. **Source chips are unlabeled dots.** The summary chips show a colored dot + name but no freshness. "gov.uk EPC Register" and "TfL API" look the same in the chip — you can't tell which is current and which is aging.

6. **Story flow is mechanical, not narrative.** The 4-step grouping (inputs → lookups → calculations → result) is structurally correct but reads like a data pipeline diagram, not a human story. Users think: "I told you my address → you found the EPC rating → that tells me the energy costs." The current flow doesn't connect these dots.

7. **No warnings or caveats.** If the commute cost is based on off-peak TfL pricing, or the council tax is estimated from a postcode rather than confirmed with the council, the user has no idea. These caveats are critical for trust.

8. **Formula line meanings are missing.** `formula.lines` has `{ label, value }` pairs but no explanation of *why* each line exists. "Stamp Duty" needs a note like "This is the tax payable on properties over £250k — first-time buyer relief was not available."

---

## Recommendations

### P0 — Must Have

| # | Change | Type | Effort | Rationale |
|---|--------|------|--------|-----------|
| 1 | **Add `human_explanation` field** to Provenance dataclass | Backend | 1–2 days | Every node should carry a 1-sentence plain-English explanation: "We looked up this property's energy rating on the government EPC Register." Currently `description` exists but is mostly empty or holds error messages. This single field eliminates the biggest cognitive barrier. |
| 2 | **Add `confidence` field** to Provenance dataclass | Backend | 0.5–1 day | Enum: `"exact"` (API fact), `"calculated"` (derived from known inputs), `"estimated"` (from user estimates), `"inferred"` (best guess). Frontend shows this as a badge so users instantly know which numbers are solid vs soft. |
| 3 | **Add `formula_line_meanings` field** to formula sub-object | Backend | 0.5–1 day | Array of plain-English strings parallel to `formula.lines`: ["Stamp duty on properties over £250k, no first-time buyer relief available", "Estimated renovation cost based on your entered estimates"]. This transforms formulas from "here's the math" to "here's the reasoning." |
| 4 | **Add component cost breakdown** for aggregate results | Backend | 1–2 days | New field `components: Array<{ label, value, percentage, confidence }>` on aggregate nodes like `total_monthly_cost`. Enables a horizontal stacked bar or breakdown list showing what % of £2,917 goes to mortgage, commute, council tax, etc. Currently the frontend has no way to show this without the backend providing the breakdown. |
| 5 | **Show confidence badges on flow cards and formula lines** | Frontend | 0.5 day | Tiny pill next to each item: "fact" (green), "calculated" (blue), "estimate" (amber). Uses the new `confidence` field. Immediately communicates which numbers are trustworthy. |
| 6 | **Show per-component freshness in summary** | Frontend | 0.5 day | Add freshness text below each source chip: "Updated today" / "8 days ago". Users currently have to switch to story or detail view to see this. |

### P1 — Should Have

| # | Change | Type | Effort | Rationale |
|---|--------|------|--------|-----------|
| 7 | **Add `warnings` field** to Provenance dataclass | Backend | 0.5 day | Array of caveat strings: ["Commute cost uses off-peak pricing; peak fares may be higher", "Council tax band estimated from postcode — verify with local council"]. Displayed as subtle amber callouts in the UI. Critical for honesty/trust. |
| 8 | **Add `step_order` integer** to Provenance dataclass | Backend | 0.25 day | Explicit ordering for story flow nodes. Currently the frontend guesses ordering from `sourceType`. With `step_order`, nodes can be explicitly sequenced regardless of type. |
| 9 | **Add `data_url_text` field** to Provenance dataclass | Backend | 0.25 day | Human-readable link text: "View on gov.uk EPC Register" instead of requiring the frontend to parse `url` into a hostname. Small but removes a rough edge. |
| 10 | **Visual cost breakdown bar** in summary view | Frontend | 1 day | Horizontal stacked bar chart using the `components` field. Shows relative weight of mortgage, commute, council tax, etc. at a glance. This is the #1 thing users want for the Total Cost scenario. |
| 11 | **Expand narrative summary with component list** | Frontend | 0.5 day | For aggregate results, the summary narrative should include a bullet list: "Your £2,917/mo includes: mortgage (£2,305), commute (£124.80), council tax (£158.79), maintenance (£305.56), insurance (£23)." Currently it's a paragraph that buries these. |
| 12 | **Improve formula boxes with meaning annotations** | Frontend | 0.5 day | Use `formula_line_meanings` to show a collapsible explanation below each formula step. Click the step → see "why this line exists." |

### P2 — Nice to Have

| # | Change | Type | Effort | Rationale |
|---|--------|------|--------|-----------|
| 13 | **Freshness timeline visualization** | Frontend | 1 day | Small horizontal timeline showing when each source was last fetched, with markers. Gives a visual sense of data freshness across the whole provenance without reading individual dates. |
| 14 | **Comparison mode** (two provenances side-by-side) | Frontend + Backend | 2–3 days | Allow comparing two properties' provenance views. Highlight differences in sources, freshness, and calculated values. Requires backend to return two provenance trees and frontend to build a split-panel layout. |
| 15 | **Interactive formula explainer** | Frontend | 1 day | Instead of a static formula box, a step-by-step "wizard" that walks through the calculation one line at a time with plain-English narration. "First, we start with the property price of £550,000..." |
| 16 | **Cache the shared reference library across views** | Frontend | 0.5 day | If a user views multiple provenances for the same property, the shared reference library (geocode, address, postcode) should be consistent and remembered. Currently each ProvenanceView instance computes its own deduplication. |
| 17 | **Add `data_quality_notes` per node** | Backend | 0.5 day | Short notes about data quality: "This API returned a 429 rate limit on first attempt; data may be from a retry." Different from warnings — these are implementation notes that could surface in a "data quality" debug panel. |

---

## Summary

**Backend changes total:** ~4–7 days across 6 new fields/changes
**Frontend changes total:** ~4–6 days across 6 UI improvements
**Combined:** ~8–13 days of work for a dramatically better non-technical user experience.

The single highest-ROI change is **#1 (human_explanation)** — it's backend-only, touches every node's `build_provenance()` method, and immediately makes the summary and story views more useful without any frontend changes.

The second highest-ROI is **#4 + #10 (component breakdown)** — the Total Monthly Cost scenario is the most common and most confusing, and a visual breakdown bar would transform it from "here's a big number, trust us" to "here's exactly what you're paying for."
