# UI/UX Design Review: Houses Property Search App

**Date:** 2026-07-15
**Review scope:** List page (property card grid) + Detail page (single property)
**Viewport tested:** 390×844 (mobile), responsive breakpoints at 600px and 960px
**Method:** Live browser inspection (ARIA snapshot, computed styles, contrast analysis) + source code audit (Vue 3 SPA components)

---

## Executive Summary

The app has a solid functional foundation with good design tokens and sensible semantic colors. However, the mobile UX reveals many small inconsistencies and missed opportunities that compound into a feeling of "functional but unpolished." The density of information per card is high, touch targets are frequently undersized, and the detail page has regressed relative to the old Jinja2 reference (missing map, stale indicators, no editorial structure).

**Overall rating: 6/10** — Functional and data-rich, but needs a focused UX pass to feel designed rather than assembled.

---

## What's Working Well

1. **Design tokens are defined** — CSS custom properties exist for all semantic colors, spacing radii, shadows, and text colors (`App.vue` lines 27-49). Great foundation.
2. **Semantic color system is coherent** — Green/green-bg (good), orange/orange-bg (warn), red/red-bg (bad), blue/blue-bg (price/accent). Consistent pattern across pills, borders, text.
3. **Card border-left indicator** — The 4px colored left border (`card__border--current` = green, `card__border--dismissed` = red) is a nice subtle status cue.
4. **Sticky header** — `position: sticky; top: 0` with `z-index: 10` keeps title and back button always accessible, even on long property lists.
5. **Responsive grid** — Single column on mobile → 2 columns at 600px → 3 columns at 960px (`PropertyList.vue` lines 53-63). Good breakpoint choices.
6. **Empty/loading/error states exist** — All three major states are handled in both list and detail views. Error state even has a Retry button on detail.
7. **Provenance tracking** — The `provenance` data model and display on each field is excellent for debugging data quality and understanding data sources.
8. **Commute leg breakdown** — Detail page shows multi-modal commute legs with mode icons, duration, cost, operator, and destination. Very useful for transit planning.

---

## Issues Found

### P0 — Must Fix (Accessibility / Usability Blockers)

#### P0-1: Touch targets consistently undersized on mobile

**Location:** List + Detail pages

**Evidence:**
| Element | Size | Required | WCAG |
|---------|------|----------|------|
| Map link `🌐` | 17×14px | 44×44px | FAIL |
| External link `↗` | 12×17px | 44×44px | FAIL |
| Back button `←` | 32×32px | 44×44px | FAIL |
| Edit buttons `✏️` | 29×24px | 44×44px | FAIL |
| Ofsted pill "Good" | 43×20px | 44×44px | FAIL height |
| Commute pills | ~73×23px | 44×44px | FAIL height |

**Violation:** [WCAG 2.2 Target Size (Minimum) 2.5.5](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html)

**Root causes:**
- Icon links are inline elements with no padding (`PropertyCard.vue` lines 85-86)
- Back button explicit `width: 32px; height: 32px` (`PropertyDetail.vue` CSS line 310)
- Edit buttons have `padding: 2px 6px` (`PropertyDetail.vue` CSS line 326)
- Commute pills use `padding: 2px 10px` (`CommutePill.vue` CSS line 56)

**Recommendation:**
- Icon links (`🌐`, `↗`): Replace `<a>` with `<a class="card__icon-link" aria-label="View on Google Maps">` with `min-width: 44px; min-height: 44px; display: inline-flex; align-items: center; justify-content: center;`
- Back button: Change to `width: 44px; height: 44px` with appropriate icon sizing.
- Edit buttons: `min-width: 44px; min-height: 44px;`.
- Commute pills: Increase font-size to 14px, change padding to `6px 12px`, add `line-height: 1.2`.

**Effort:** Quick win — CSS-only changes across 3 component files.

---

#### P0-2: No `<main>` landmark, semantic HTML gaps on detail page

**Location:** `PropertyDetail.vue` + `PropertyList.vue`

**Evidence:** `document.querySelectorAll('[role="main"], main').length === 0`. The detail page uses 0 `<article>` elements. The list page correctly uses `<article>` for cards.

**Impact:** Screen reader users lose primary navigation landmark. Content regions are not semantically distinguished.

**Recommendation:**
- Replace `<div class="page">` with `<main class="page">` in both views.
- Add `role="main"` as fallback.

**Effort:** Quick win — 2 template edits.

---

### P1 — Should Fix (Significant UX Impact)

#### P1-1: Missing embedded map on detail page (regression from reference UI)

**Location:** `PropertyDetail.vue` — Location section

**Evidence:** The old Jinja2 reference (`docs/current-ui/detail-page.html` lines 47-53) has an interactive Leaflet map with click-to-set coordinates. The Vue SPA has none.

**Impact:** Users cannot visually verify property location without opening Google Maps in a new tab.

**Recommendation:**
- Add a Leaflet static or interactive map in the Location section showing the property pin.
- Alternative: Use a static map tile image linking to Google Maps for a lightweight solution.
- Re-implement the map picker for coordinate editing.

**Effort:** Significant rework (Leaflet dependency + map component).

---

#### P1-2: `--text-muted` (#999) fails WCAG AA on white backgrounds

**Location:** Throughout all views. Used for `var(--text-muted)` and `var(--muted)` (~#9e9e9e).

**Evidence:** Computed contrast ratio: **2.85:1** against white card background. WCAG AA requires 4.5:1 for normal text.

**Affected elements:**
- `.detail__provenance` — `font-size: 0.75em` (~12px) at #9e9e9e
- `.commute-leg__duration` — `color: var(--muted)`
- `.card__external-link` — `color: var(--text-muted)` at 13px
- `.empty-state__text` — `color: var(--text-muted)` at 16px
- `.commute-leg__cost`, `commute-leg__operator` — `color: var(--muted)`

**Recommendation:**
- Change `--text-muted` from `#999` to `#757575` (4.67:1 on white — passes AA for 14px+ text).
- Change `--muted` from `#9e9e9e` to `#757575` for consistency.
- For very small text (11-12px), use `#666` (5.74:1 on white).

**Effort:** Quick win — 2 variable changes in `App.vue`.

---

#### P1-3: Ofsted rating "Good" mapped to orange warning pill

**Location:** `frontend/src/utils/format.ts` lines 5-12

**Code:**
```ts
export function ofstedClass(rating: string): string {
    switch (simpleOfsted(rating)) {
        case 'Outstanding': return 'pill--good'   // green
        case 'Good':        return 'pill--warn'   // orange — misleading!
        default:            return 'pill--muted'  // gray
    }
}
```

**Problem:** "Good" is a positive rating shown in orange (warning/alert semantic). Meanwhile "Requires Improvement" gets gray (neutral/muted) — no negative indicator at all. This creates a false semantic mapping that misleads users scanning for red flags.

**Recommendation:**
- Outstanding → `pill--good` (green)
- Good → `pill--good` (green) or a lighter green variant
- Requires Improvement → `pill--warn` (orange)
- Inadequate → `pill--bad` (red)
- Unknown → `pill--muted` (gray)

**Effort:** Quick win.

---

#### P1-4: Detail summary bar lacks visual hierarchy

**Location:** `PropertyDetail.vue` template lines 132-145

**Evidence:** Summary bar shows address, price, bedrooms, monthly cost — all in one `display: flex; flex-wrap: wrap;` line.

Problems:
- Address (up to 30 chars) wraps awkwardly inline
- Price (`span.detail__price-value`) and monthly cost (`span.detail__monthly`) both have `font-weight: 600` in competing accent colors (green vs blue)
- Address is a `<span>` — not an `<h2>`, breaking document outline
- No semantic grouping

**ARIA snapshot output:**
```
- generic: Thurlby Way, Maidenhead, SL6 3YZ
- generic: £650,000
- generic: 4 bed
- generic: £3240.59/mo
```
— four equally-weighted generic text nodes, no structural hierarchy.

**Recommendation:**
- Restructure as two lines:
  - Line 1: `<h2 class="detail__address">` (18-20px bold, primary text color)
  - Line 2: Price (green, bold, 22-24px) + Bedrooms (secondary, 14px) + Monthly cost (de-emphasized, right-aligned)
- Use `margin-left: auto` on monthly cost to push it right on the second line.

**Effort:** Quick win.

---

#### P1-5: Back button has no `aria-label`, 50% opacity makes it easy to miss

**Location:** `PropertyDetail.vue` line 114 + CSS line 320

**Evidence:** `<button class="btn--icon" @click="router.push('/')">←</button>` with CSS `opacity: 0.5`. The `←` character may not be announced by screen readers.

**Recommendation:**
- Add `aria-label="Back to property list"`.
- Change default opacity to 0.7.
- Keep `opacity: 1` on hover/focus.

**Effort:** Quick win.

---

#### P1-6: No data freshness indicators (regression from reference UI)

**Location:** Detail page

**Evidence:** Old Jinja2 UI has `.stale-spinner` and `.stale-spinner--fresh` CSS (`docs/current-ui/detail.css` lines 93-105). The Vue SPA has none. Users cannot tell if data was fetched 30 seconds or 30 days ago.

**Impact:** For a research/decision-making tool, data freshness is critical — stale commute data or prices undermine trust.

**Recommendation:**
- Add a subtle "Updated X mins ago" indicator in each section header or in the summary bar.
- Display the provenance label per field with a timestamp when available.

**Effort:** Moderate — new component for time-since display.

---

### P2 — Nice to Fix (Polish / Consistency)

#### P2-1: Inconsistent horizontal page padding

**Location:** `PropertyList.vue` CSS line 38 vs `PropertyDetail.vue` CSS line 307

| View | Padding |
|------|---------|
| List page | `12px 12px 40px` |
| Detail page | `12px 16px 40px` |

**Recommendation:** Standardize to `12px 16px` on both.

**Effort:** Quick win.

---

#### P2-2: Card section dividers create visual noise

**Location:** `PropertyCard.vue` CSS — `.card__row--section` has `border-top: 1px solid #eee`

**Evidence:** A card can have up to 4 section borders (address/commute/schools/financial). Combined with the 12px border-radius, small font sizes, and tight spacing, the card feels busy.

**Recommendation:**
- Use `4px` or `8px` vertical gap instead of borders between sections.
- Reserve the thin border for the most important divider (e.g., before financial total).
- Increase the gap between sections from `4px` to `8px`.

**Effort:** Quick win.

---

#### P2-3: Commute pills on list cards omit cost information

**Location:** `PropertyCard.vue` template — `CommutePill` calls pass `:cost="null"`

**Evidence:** The detail page shows commute costs. On the list page, users must drill into each property to compare commute costs — a key decision factor.

**Recommendation:**
- Pass actual commute cost to `CommutePill` on the list page when available.
- If cost data adds noise, show it only for the user's primary commute.

**Effort:** Moderate — data propagation from store.

---

#### P2-4: Card list uses `flex` on mobile but switches to `grid` at tablet

**Location:** `PropertyList.vue` CSS lines 41-58

**Evidence:**
```css
.card-list { display: flex; flex-direction: column; gap: 12px; }
@media (min-width: 600px) { .card-list { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; } }
```

Unnecessary `flex` → `grid` switch. Grid supports single column trivially.

**Recommendation:** Unify to `display: grid` at all breakpoints: `grid-template-columns: 1fr` → `1fr 1fr` → `1fr 1fr 1fr`.

**Effort:** Quick win.

---

#### P2-5: Empty state has no call-to-action

**Location:** `PropertyList.vue` line 24

**Current text:** `"No properties yet. Add one via the browser extension."`

**Issue:** No link, no guidance on where to find the extension.

**Recommendation:** Add a link to the extension page or change to "No properties yet. Use the browser extension to add properties from Rightmove."

**Effort:** Quick win.

---

#### P2-6: Inconsistent loading states across detail sections

**Location:** `PropertyDetail.vue` — EPC section (lines 231-245) vs other sections

**Evidence:** EPC section shows "Loading..." text. Other sections (Affordability, Schools) show `?` or `—` placeholders when data is pending. No visual loading indicator.

**Recommendation:**
- Add a shared loading state pattern: pulsing skeleton lines for in-progress data.
- At minimum, use consistent "Loading..." text with a subtle pulse animation.

**Effort:** Moderate — new `SkeletonLoader` component.

---

#### P2-7: Color-only indicators for commute severity

**Location:** Commute pills — `pill--good`/`--warn`/`--bad`

**WCAG:** Use of Color (1.4.1) — color must not be the sole means of conveying information.

**Mitigation:** The mode text ("walk", "transit", "drive") provides some secondary differentiation, but not for severity. Two 40m walks could be green and red based on thresholds — a colorblind user sees no difference.

**Recommendation:**
- For severe commutes (red), add a `⚠️` or `❗` indicator.
- Or add minute value prominence: the duration is the primary value and already numeric — but ensure it's visually prominent enough to be the cue.

**Effort:** Quick win.

---

#### P2-8: Missing `:focus-visible` styles

**Location:** All CSS

**Evidence:** No `:focus-visible` declarations exist. Keyboard users get only browser default outlines, which may be invisible on some elements.

**Recommendation:** Add global rule:
```css
:focus-visible {
  outline: 2px solid var(--blue);
  outline-offset: 2px;
}
```

**Effort:** Quick win.

---

#### P2-9: Emoji-heavy section headers on detail page

**Location:** `PropertyDetail.vue` — all 6 section headers use leading emoji

**Headers:** `🚆 Commutes`, `📍 Location`, `🏫 Schools`, `⚡ EPC`, `💰 Affordability`, `📝 Comments`

**Issues:**
- Inconsistent rendering across platforms
- Screen readers may read emoji descriptions aloud, adding auditory clutter
- Six prominent emojis on one scroll feel busy for a data tool

**Recommendation:**
- Remove emojis or keep only 1-2 for the most important sections (Commutes, Affordability).
- Apply `aria-hidden="true"` to remaining emojis.
- Use typographic hierarchy (font-weight + size + background tint) instead of emojis.

**Effort:** Quick win.

---

## Consistency Comparison: Vue SPA vs Old Jinja2 Reference

| Feature | Old UI (Jinja2) | New UI (Vue SPA) | Assessment |
|---|---|---|---|
| Embedded Leaflet map | ✅ Yes (interactive) | ❌ Missing | **Regression** |
| Stale/fresh indicator | ✅ Yes | ❌ Missing | **Regression** |
| Provenance badges | ✅ Styled badges | ✅ Raw text | Adequate but less polished |
| Loading states | ✅ Spinner | ✅ Loading text | Different but functional |
| Error states | ✅ Error page | ✅ With retry button | Improved |
| Map picker | ✅ Click-to-set | ❌ Missing | **Regression** |
| Back navigation | Browser back | Custom button | Functional |
| Editable fields | HTMX inline edit | Vue inline edit | Equivalent |
| Responsive design | ✅ Same tokens | ✅ Same tokens | Consistent |

**Key regressions:** Embedded map and stale indicators are the most consequential gaps.

---

## Accessibility Scorecard

| Criterion | Status | Detail |
|---|---|---|
| Body text contrast (#1a1a1a on #f4f5f7) | ✅ PASS 15.95:1 | Excellent |
| Link contrast (#1565c0 on white) | ✅ PASS 5.75:1 | Passes AA |
| Muted text (#999 / #9e9e9e) | ❌ FAIL 2.85:1 | Needs darkening to #757575 |
| Green pill on green-bg | ✅ PASS 4.56:1 | AA pass |
| Orange pill on orange-bg | ⚠️ 3.46:1 | AA-large only |
| Red pill on red-bg | ✅ PASS 4.92:1 | AA pass |
| Touch targets (44×44px) | ❌ 5 elements failing | Back btn, icons, edit btns, pills |
| `<main>` landmark | ❌ Not present | Missing from both views |
| ARIA labels | ❌ Back btn, map links missing | Add `aria-label` |
| `:focus-visible` styles | ❌ Not implemented | Add global rule |
| Semantic HTML (articles) | ✅ List page | Detail page missing |
| Color-only info | ⚠️ Partial | Mode text helps but severity is color-only |
| Emoji repetition | ⚠️ 6 emojis on detail | Consider removal |

---

## Recommendations Summary

### Quick Wins (CSS/text changes, <30 min) — 15 items
1. [P0-1] Increase all touch targets to min 44×44px
2. [P0-2] Add `<main>` landmark, missing ARIA labels
3. [P1-2] Darken `--text-muted` from #999 to #757575
4. [P1-3] Fix Ofsted `Good` → green, `Requires Improvement` → orange
5. [P1-4] Restructure detail summary bar into two visual tiers
6. [P1-5] Add `aria-label` to back button, bump opacity
7. [P2-1] Unify horizontal page padding to 12px 16px
8. [P2-2] Replace card section borders with vertical gap spacing
9. [P2-4] Unify card list to `display: grid` at all breakpoints
10. [P2-5] Add browser extension link in empty state text
11. [P2-7] Add icon supplement for severe commute pills
12. [P2-8] Add `:focus-visible` global styles
13. [P2-9] Remove or aria-hide emoji section headers
14. [P1-4] Convert detail address to `<h2>` heading
15. [P0-2] Add `<article>` wrapper around detail page

### Moderate Effort (new components, <2h) — 3 items
16. [P1-6] Add data freshness/stale indicators
17. [P2-3] Show commute costs on list card pills
18. [P2-6] Add skeleton loading placeholders for detail sections

### Significant Rework (integration work, >2h) — 2 items
19. [P1-1] Re-add Leaflet interactive map to detail page
20. [P1-1] Re-implement map picker for coordinate editing

---

## Final Assessment

**Grade: 6/10 — Functional, needs UX polish**

The app is data-rich and the architecture (design tokens, store, data model, routing) is solid. The main UX issues are:

1. **Mobile touch targets** — The most critical fix. Multiple interactive elements are well below the 44×44px minimum, making the app frustrating on a phone.
2. **Regressed features** — The map, stale indicators, and some polish from the Jinja2 reference are missing, making the new SPA feel less capable despite the better architecture.
3. **Visual hierarchy** — The detail page presents all information at equal weight. The list cards are dense with section dividers. Both need better typographic hierarchy.
4. **Accessibility gaps** — Muted text contrast failures, missing landmarks, no focus styles, aria-label gaps. None are hard to fix.

The **highest-impact changes** in order of effort-per-value:
1. Touch target fixes (P0-1) — a few CSS properties transform mobile usability
2. `--text-muted` darkening (P1-2) — one CSS variable fixes all instances
3. Detail summary hierarchy (P1-4) — makes the most important data scannable at a glance
