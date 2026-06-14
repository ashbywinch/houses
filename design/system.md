# Houses — Design System

## Colours

```
Primary Navy:     #1a2a3a  (headers, nav)
Card Background:  #ffffff  (cards)
Page Background:  #f4f5f7  (page chrome)

Rating — Green:   #2e7d32  (good: A-B EPC, Outstanding Ofsted, <45min commute, <15min walk, >=2023 inspection)
Rating — Orange:  #e65100  (warn: C-D EPC, Good Ofsted, 45-75min commute, 15-30min walk, <=2022 inspection)
Rating — Red:     #c62828  (bad: E-G EPC, RI/Inadequate Ofsted, >75min commute, >30min walk)
Rating — Grey:    #9e9e9e  (missing data)

Text Primary:     #1a1a1a
Text Secondary:   #666666
Text Muted:       #999999
Border:           #e0e0e0
Divider:          #eeeeee
```

## Typography

```css
font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
base:     16px / 1.5   (body)
small:    13px / 1.4   (metadata)
heading:  20px / 1.3   (section titles)
title:    24px / 1.3   (page titles, property address)
```

## Spacing Scale (8px grid)

```
xs:   4px
s:    8px
m:    16px
l:    24px
xl:   32px
xxl:  48px
```

## Component Library

### Badge
Colour-coded pill. Used for EPC ratings, Ofsted grades, commute durations, Status.

```html
<span class="badge badge--good">B</span>
<span class="badge badge--warn">D</span>
<span class="badge badge--bad">F</span>
<span class="badge badge--muted">--</span>
<span class="badge badge--status badge--status-maybe">Maybe</span>
<span class="badge badge--status badge--status-no">No</span>
```

### Card
White rounded rectangle with shadow. Used for property cards and as section container.

```
background: #fff
border-radius: 12px
padding: 16px
box-shadow: 0 1px 3px rgba(0,0,0,0.08)
```

### Property Card (list item)
Five data lines, each omitted if the data doesn't exist for that property.

```
[Address, truncated]                   [£Price]  [◇]
[Bedrooms] · [bathrooms] · [postcode district]
[Simon ◈m 🟢] · [Lorena ◈m 🟡] · [Bracknell ◈m 🟢]       ← commutes
[School Name 🟢🟢]  [School Name 🟡🟢]                     ← schools (2 dots each)
[Financial impact] · [Walk ◈m 🟢]                          ← financial + area
```

**Status dot:** ◇ blue = Maybe, grey outline = undecided, 🏠 green = current home
**Current home:** green left border, no schools shown, shows absolute total
**Dismissed (No):** hidden by default behind "Show dismissed (n)" toggle. Red left border, dimmed.

### School Dual Dot
Two colour-coded dots per school on the card:
- **Dot 1** (Ofsted quality): 🟢 Outstanding, 🟡 Good, 🔴 RI/Inadequate, grey = missing
- **Dot 2** (Walk/Bus time): 🟢 <15m, 🟡 15-30m, 🔴 >30m, grey = missing

Hover/tap dot 1: `"Ofsted: Outstanding (2023)"` — rating + inspection year
Hover/tap dot 2: `"Walk: 12 min"` or `"⚠ Walk estimated from postcode centroid"`

### Commute Pill
Colour-coded duration per person. Also on hover/tap shows route summary and data quality.

```
Simon 32m 🟢
```
🟢 <45m, 🟡 45-75m, 🔴 >75m (Simon/Lorena)
🟢 <30m, 🟡 30-60m, 🔴 >60m (Bracknell)
Missing commute (no data): `Simon --` in grey

### Financial Line
```
~£2,850/mo · +£800 vs now · Walk 12m 🟢
```

Three states:
1. **Full data:** `~£2,850/mo · +£800 vs now · Walk 12m 🟢`
2. **No baseline:** `~£2,850/mo est. · Walk 12m 🟢`
3. **Not enriched:** `£450,000 · Walk 12m 🟢` (price as proxy, no cost)
4. **Current home:** `Total monthly: £3,100/mo` (absolute, no delta, no walk)

### Section (detail page)
Collapsible group with title, summary value/badge, and expand arrow.

```
▼ Key Info           EPC B · Band D    >
  £450,000 · 3 bed · 2 bath
  Council Tax: £1,800/yr
  [Map link]

▶ Commute & Area     Simon 32min ✓      >
▶ Schools            Outstanding ✓      >
▶ Affordability      £2,850/mo          >
▶ Notes & Inputs     2 fields set       >
```

### Expandable Section
Click header to toggle content. Content loads either:
- **Inline present** (always in DOM, just CSS `display: none/block`)
- **HTMX lazy-load** (`hx-trigger="revealed"` for large content)

### Commute Row
One person's commute in a compact row.

```
🚶 Simon → Victoria           32 min   £4.50
  🚇 Bakerloo to Oxford Circus (8m) → 🚇 Victoria to Victoria (5m)
```

### School Block
```
🏫 St Mary's Primary        0.8 km  12 min walk
  Ofsted: Outstanding     Inspection: 2023
```

### Affordability Line
```
Mortgage Payment                £1,200/mo
Sinking Fund                    £150/mo
Life Insurance                  £25/mo
Commute Cost                    £320/mo
Council Tax                     £150/mo
───────────────────────────────────────
Total Monthly Housing           £1,845/mo  ↑bold
```

### Form Elements
```
Input:     border 1px solid #ddd, border-radius 8px, padding 12px
Select:    same as input, custom chevron
Textarea:  same as input
Button:    bg=#1a2a3a, color=white, border-radius 8px, padding 12px 24px
           hover: bg=#2a3a4a
           secondary: bg=white, border=1px #ddd, color=#333
```

## Rating Rules

| Domain | Good (green) | Warn (orange) | Bad (red) |
|---|---|---|---|
| EPC | A–B | C–D | E–F–G |
| Commute (Simon/Lorena) | <45 min | 45–75 min | >75 min |
| Commute (Bracknell) | <30 min | 30–60 min | >60 min |
| Walk time | <15 min | 15–30 min | >30 min |
| Ofsted | Outstanding | Good | RI / Inadequate |
| Inspection Year | >=2023 | <=2022 | — |
| Status | Maybe (blue) | — | No (red) |

## Layout Breakpoints

```
Mobile:   0–639px     (single column cards, stacked)
Tablet:   640–1023px  (2-column card grid, wider detail)
Desktop:  1024px+     (3-column card grid, max-width 1200px content)
```

## HTMX Interaction Patterns

| Pattern | Implementation |
|---|---|
| Expand section | `hx-get="/properties/{rid}/section/{id}" hx-trigger="click" hx-target="#section-{id}" hx-swap="innerHTML"` |
| Sort list | `hx-get="/properties?sort={field}" hx-target="#property-list" hx-trigger="change"` |
| Lazy load detail | `hx-get="/properties/{rid}/detail" hx-trigger="revealed" hx-target="#detail"` |
| Submit form | `hx-post="/properties" hx-target="#main" hx-swap="innerHTML"` |
| Inline edit | `hx-put="/properties/{rid}/status" hx-trigger="change" hx-target="#status-badge"` |
