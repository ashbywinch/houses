# Houses — Screen Wireframe Descriptions

> All screens are mobile-first responsive. Layouts are described for 375px width, with desktop adaptations noted.

---

## Screen 1: Property List

### Purpose
Glanceable overview of every house in the system. Users scan this to:
- See which houses are still active (Maybe or undecided)
- Spot the best commute/price/school combination at a glance
- See the financial delta vs their current home
- Tap through to a property for full detail

### Layout (mobile, 375px)

```
┌───────────────────────────────────────────────┐
│ Houses  [+ Add]        [Show dismissed (3) ▾] │  ← header bar
│                                                │
│ ┌───────────────────────────────────────────┐ │
│ │ 48 Acacia Avenue             £450,000  ◇ │ │  ← active card (Maybe)
│ │ 3 bed · 1 bath · SW1                      │ │
│ │                                             │ │
│ │ Simon 32m 🟢 · Lorena 45m 🟡 · Bracknell 22m🟢│  ← commutes
│ │                                             │ │
│ │ St Mary's Primary 🟢🟢  The Academy 🟡🟢   │ │  ← schools (2 dots each)
│ │                                             │ │
│ │ ~£2,850/mo  +£800 vs now  · Walk 12m 🟢    │ │  ← financial + area
│ └───────────────────────────────────────────┘ │
│                                                │
│ ┌───────────────────────────────────────────┐ │
│ │ 7 Oak Lane                 £380,000  ◇   │ │  ← active card (Maybe)
│ │ 3 bed · 1 bath · SE15                     │ │
│ │                                             │ │
│ │ Simon 18m 🟢 · Lorena 62m 🔴 · Bracknell 12m🟢│
│ │                                             │ │
│ │ St George's Primary 🟢🟢  The Academy 🟡🟢 │ │
│ │                                             │ │
│ │ ~£2,100/mo  +£200 vs now  · Walk 8m 🟢     │ │
│ └───────────────────────────────────────────┘ │
│                                                │
│ ┌ Your current home ────────────────────────┐ │
│ │ 12 Maple Road, NW5          £525,000  🏠 │ │  ← current home card
│ │ 4 bed · 2 bath                            │ │
│ │                                             │ │
│ │ Commute: Simon 55m 🟡 · Lorena 38m 🟢     │ │  ← only 2 commutes
│ │                                             │ │
│ │ Total monthly: £3,100/mo                   │ │  ← baseline cost (no schools shown)
│ └───────────────────────────────────────────┘ │
│                                                │
│ ┌─── toggled: Show dismissed (3) ──────────┐ │
│ │ ╔═══════════════════════════════════════╗ │ │  ← red left border
│ │ ║ 15 Victoria Road          £325,000   ║ │ │  ← dimmed card
│ │ ║ 2 bed · 1 bath · E8                   ║ │ │
│ │ ║                                       ║ │ │
│ │ ║ Simon 32m 🟢 · Lorena 55m 🟡         ║ │ │
│ │ ║                                       ║ │ │
│ │ ║ ~£2,400/mo  +£400 vs now             ║ │ │
│ │ ╚═══════════════════════════════════════╝ │ │
│ │  (2 more dismissed — tap to expand)       │ │
│ └───────────────────────────────────────────┘ │
└───────────────────────────────────────────────┘
```

### Card Structure

Each card has 5 data lines + optional footer:

```
Line 1: [Address, truncated]                [Price, £]  [◇/🏠]
Line 2: [Bedrooms] · [bathrooms] · [postcode district]
Line 3: [Simon ◈m 🟢] · [Lorena ◈m 🟡] · [Bracknell ◈m 🟢]
Line 4: [School Name] 🟢🟢  [School Name] 🟡🟢   ← omitted if no schools
Line 5: [Financial impact] · [Walk ◈m 🟢]           ← walk omitted if unknown
```

Legend:
- `◇` = status maybe (blue), `◇` unset (grey), `🏠` = current home (green)
- Commute pills colour: 🟢 <45min, 🟡 45-75min, 🔴 >75min (Simon/Lorena); 🟢 <30min, 🟡 30-60min, 🔴 >60min (Bracknell)
- School dual dots: first = Ofsted colour (🟢 Outstanding, 🟡 Good, 🔴 RI/Inadequate), second = walk time colour (🟢 <15m, 🟡 15-30m, 🔴 >30m)
- Walk to town: 🟢 <15m, 🟡 15-30m, 🔴 >30m

### Key Components and Interaction

| Element | Behaviour |
|---|---|
| Status dot ◇ | `◇` blue for Maybe, unset (grey outline) for undecided. Tap cycles Maybe/Nothing/No |
| Price | Bold, right-aligned. Acts as cost proxy when total unknown. |
| Commute pills | Colour-coded per person. Tap opens commute overlay with leg breakdown + source |
| School name | Tap opens school detail popup |
| School Ofsted dot 🟢 | Hover/tap: "Ofsted: Outstanding (2023)" — shows rating + inspection year |
| School walk dot 🟢 | Hover/tap: "Walk: 12 min" or "⚠ Walk estimated from postcode" — shows time + accuracy warning |
| Financial line | Shows total + delta vs current, or price as proxy if unknown |
| Walk badge 🟢 | Hover/tap: "Walk to town: 12 min" |
| Current home card | Green left border, 🏠 icon, no schools shown (not relevant), provides baseline cost for delta calculation |
| Card tap | Navigates to Property Detail |
| ＋ Add button | Opens Add Property form |
| "Show dismissed" toggle | Reveals No cards with red left border + reduced opacity |

### Financial Line States

| State | Example |
|---|---|
| All known + baseline exists | `~£2,850/mo · +£800 vs now · Walk 12m 🟢` |
| Total known, no baseline | `~£2,850/mo est. · Walk 12m 🟢` |
| Total unknown (not enriched) | `£450,000 · Walk 12m 🟢` (price only, no cost) |
| Total unknown + no walk either | Show just `£450,000` — no financial line |
| Current home itself | `Total monthly: £3,100/mo` (absolute, no delta) |

### States

| State | Behaviour |
|---|---|
| Loading | Skeleton cards (3 grey rectangles with shimmer) |
| Empty | "No houses yet. Paste a Rightmove link to get started." + Add button |
| Error | "Failed to load properties." + retry button |
| Current home | Green left border, `🏠` badge, commute shown, no schools, absolute total |
| Dismissed toggled on | Red left border cards appear at bottom, dimmed |
| Migrated to DAG | Same cards, same layout. Dots gain provenance tap targets |

### Desktop Adaptation (≥1024px)

2-column card grid. Cards wider, address not truncated. School dots show tooltip on hover (no tap needed). "Show dismissed" becomes a persistent sidebar or inline section with expand/collapse. Sort controls in the header row (sort by price, commute time, added date).

---

## Screen 2: Property Detail

### Purpose
Full picture of one house. Users scroll through 5 expandable sections to understand everything about a property. Each section shows a summary indicator (good/middling/bad) and can be expanded for detail and provenance.

### Layout (mobile, 375px)

```
┌─────────────────────────────────────┐
│ ← Properties    48 Acacia Avenue    │  ← header with back
│                                     │
│ EPC B  ·  Maybe                     │  ← sticky summary bar
│ £450,000 · 3 bed · 1 bath           │
│                                     │
│ ┌─ ▼ Key Info ───────────────────┐ │
│ │ £450,000    3 bedrooms          │ │  ← always expanded
│ │ Council Tax: Band D · £1,800/yr │ │
│ │ EPC: B  (good)                  │ │
│ │ 📍 Map link                     │ │
│ └─────────────────────────────────┘ │
│                                     │
│ ┌─ ▼ Commute & Area ─────────────┐ │
│ │ 🚶 Simon → Victoria           │ │  ← summary
│ │   32 min · £4.50/day           │ │
│ │   🟢 good                      │ │
│ │ 🚶 Lorena → Aldgate            │ │
│ │   45 min · £2.80/day           │ │
│ │   🟡 middling                  │ │
│ │ 🚗 Bracknell                   │ │
│ │   22 min · £6.00/day           │ │
│ │   🟢 good                      │ │
│ │ ───── expanded ──────────────  │ │
│ │ Simon: Bakerloo (8m) →         │ │
│ │   Victoria line (5m)           │ │
│ │   Source: TfL API              │ │
│ │   ⓘ calculated 2026-06-01     │ │
│ │ ─────────────────────────────  │ │
│ │ Area description...            │ │
│ │ Walk to town: 12 min 🟢        │ │
│ │ Amenities: Co-op, park, GP     │ │
│ └─────────────────────────────────┘ │
│                                     │
│ ┌─ ▶ Schools ────────────────────┐ │
│ │ Primary: St Mary's (0.8 km)    │ │  ← collapsed
│ │   Outstanding 🟢                │ │
│ │ Secondary: The Academy (2.1 km) │ │
│ │   Good 🟡                       │ │
│ └─────────────────────────────────┘ │
│                                     │
│ ┌─ ▶ Affordability ──────────────┐ │
│ │ Total: £2,850/mo               │ │  ← collapsed, shows total
│ └─────────────────────────────────┘ │
│                                     │
│ ┌─ ▶ Notes & Inputs ─────────────┐ │
│ │ Status: Maybe · 2 notes        │ │  ← collapsed
│ └─────────────────────────────────┘ │
└─────────────────────────────────────┘
```

### Expand/Collapse Pattern

Each section header is a tap target. Tap toggles expansion. The open section pushes content below — no modals, no overlays. Uses `hx-get` to lazy-load section content if it's not already in the DOM.

**Section loading strategy:**
- Slice 1 (sheet-backed): All section content rendered server-side, hidden with CSS. Expand = toggle `display`.
- Future (DAG-backed): Sections can lazy-load individually via `hx-trigger="revealed"` for the expanded state, reducing initial page weight.

### Section: Commute Drill-Down (expanded)

The commute section expands to show:
- Each person's commute as a block
- Leg-by-leg breakdown: walk → tube → walk → train with durations
- Source tag: "TfL API" or "National Rail Fares" with green/red indicator
- Daily cost and yearly cost
- ⓘ icon to show provenance (API name, timestamp, fallback path, status code)

### Section: Affordability Drill-Down (expanded)

```
Mortgage Payment                £1,200/mo
Sinking Fund                    £150/mo
Life Insurance                  £25/mo
Commute Cost                    £320/mo
Council Tax                     £150/mo
───────────────────────────────────────
Total Monthly Housing           £1,845/mo  ← bold, large

Stamp Duty:              £11,250       (one-time)
Net Ashby Contribution:  £58,333
Mortgage Required:       £225,000
```

Each line item shows its source: formula or API. On DAG, clicking a line item navigates to the graph for that node.

### Section: Notes & Inputs (expanded)

```
Status:     [ Maybe ▼ ]
Reason:     [___________________________]

Design Needed:  [ Yes ▼ ]
Planning Needed: [ No ▼ ]

Ashby Works Est: [ £________ ]

Group Notes:
┌─────────────────────────────────┐
│ Simon: Booked viewing Sat      │
│ Lorena: Need to check parking  │
└─────────────────────────────────┘

Ashby Comments:
┌─────────────────────────────────┐
│ Needs new boiler. Check         │
│ structural survey.              │
└─────────────────────────────────┘
```

### Section: Provenance (DAG-only, hidden in Slice 1)

When DAG is active, each value in every section shows a small ⓘ icon. Tap opens an overlay:

```
┌── Provenance ──────────────────────┐
│ Simon Commute Time                 │
│ 32 min                             │
│                                    │
│ Source:    TfL API                 │
│ Status:    ✓ ok                    │
│ Computed:  2026-06-01 14:32:01     │
│ Fallback:  TfL returned empty      │
│            → National Rail fares   │
│ Dependencies:                      │
│   • property_location              │
│   • simon_office_location          │
└────────────────────────────────────┘
```

---

## Screen 3: Add Property

### Purpose
Enter a new house into the system. Minimal form — paste a Rightmove URL, the rest is scraped.

### Layout

```
┌─────────────────────────────────────┐
│ ← Houses    Add Property            │
│                                     │
│ Rightmove Link                      │
│ ┌─────────────────────────────────┐ │
│ │ https://www.rightmove.co.uk/...│ │
│ └─────────────────────────────────┘ │
│                                     │
│ Or fill in manually:                │
│ Address                             │
│ ┌─────────────────────────────────┐ │
│ │                                 │ │
│ └─────────────────────────────────┘ │
│                                     │
│ Postcode     │ Price     │ Beds    │
│ ┌──────────┐ │ ┌──────┐ │ ┌────┐ │ │
│ │          │ │ │      │ │ │    │ │ │
│ └──────────┘ │ └──────┘ │ └────┘ │ │
│                                     │
│ [      Add House       ]           │
└─────────────────────────────────────┘
```

### Flow: Post-submit

```
[Add House] → Loading state:
  ┌── Adding ─────────────────────┐
  │ ✓ Scraping Rightmove page    │
  │ ◌ Geocoding address...       │
  │ ◌ Computing commutes...      │
  │ ◌ Looking up schools...      │
  │ ◌ Checking EPC...            │
  │ ◌ Council tax lookup...      │
  └──────────────────────────────┘

Each module completes in real-time via SSE or HTMX polling.
On error: ✕ TfL API returned 402 (retrying...)
On complete: navigates to Property Detail.
```

The progress view is critical — enrichment takes 15–60 seconds. Users need to see it's working. Each module that completes turns green, current one shows a spinner, failed ones show red with the error.

---

## Screen 4: User Config

### Purpose
Set up user-specific constants that are currently hardcoded: office locations, car ownership, trip frequencies, deposit shares, child details.

### Layout

```
┌─────────────────────────────────────┐
│ ← More     Configuration            │
│                                     │
│ ┌─ People ────────────────────────┐ │
│ │ Simon:                          │ │
│ │   Office: ░Pimlico/Victoria░    │ │
│ │   Car:    [Yes] [○ No]          │ │
│ │   Trips/wk: [ 5 ░░ ]           │ │
│ │                                 │ │
│ │ Lorena:                         │ │
│ │   Office: ░░░Aldgate░░░░░       │ │
│ │   Car:    [○ Yes] [No]          │ │
│ │   Trips/wk: [ 5 ░░ ]           │ │
│ │                                 │ │
│ │ Child:                          │ │
│ │   Age: [ 7 ░░ ]                 │ │
│ │   Gender: ░░Boy░░░░             │ │
│ └─────────────────────────────────┘ │
│                                     │
│ ┌─ Finances ──────────────────────┐ │
│ │ Total Deposit:   [£ 100,000  ]  │ │
│ │ Ashby Portion:   [£ 33,333   ]  │ │
│ │ Mortgage Rate:   [ 4.5%     ]   │ │
│ │ Mortgage Term:   [ 25 years  ]  │ │
│ │ Sinking Fund %:  [ 0.4%     ]   │ │
│ │ Life Insurance:  [£ 30/mo   ]   │ │
│ │ Rental Income:   [£ 0/mo    ]   │ │
│ └─────────────────────────────────┘ │
│                                     │
│ ┌─ About ─────────────────────────┐ │
│ │ Property count: 12              │ │
│ │ Last enrichment: 2026-06-14     │ │
│ │ Data source: Google Sheets      │ │
│ └─────────────────────────────────┘ │
└─────────────────────────────────────┘
```

Changes to config should trigger a note: "These changes affect affordability calculations. Re-enrich affected properties?" with a button to batch re-enrich.

---

## Screen 5: Group Drill-Down (Future / DAG)

### Purpose
Full provenance for one data domain. When DAG is active, tapping a section header or ⓘ icon opens the drill-down view showing every value, its source, fallback chain, and intermediate calculations.

### Layout (modal/page overlay)

```
┌── Commute Detail ──────────────────┐
│ ✕                                  │
│                                     │
│ Simon → Victoria                    │
│ ─────────────────────────────────── │
│ Duration:   32 min  🟢 good        │
│   Source:   TfL Route API          │
│   Status:   ✓ ok                   │
│   Route:    Bakerloo (8m) → Vic (5m)│
│             walk from property (12m)│
│             walk to office (7m)     │
│                                     │
│ Daily Cost: £4.50  🟢              │
│   Source:   TfL Fare API           │
│   Status:   ✓ ok                   │
│   Breakdown: Zone 1-2 cap: £4.50  │
│                                     │
│ ── Lorena → Aldgate ────────────── │
│ Duration:   45 min  🟡 middling    │
│   Source:   TfL Route API          │
│   Status:   ✓ ok                   │
│   ⚠ Fallback: Google Routes was    │
│     used for walking time          │
│                                     │
│ ── Bracknell ───────────────────── │
│ Duration:   22 min  🟢             │
│   Source:   Google Routes API      │
│   Status:   ✓ ok                   │
│                                     │
│ ── Yearly Cost ─────────────────── │
│ £3,677/yr                         │
│   Formula: (simon + lorena +       │
│   bracknell) × 46 trips ÷ 12       │
└────────────────────────────────────┘
```

Each value has a grey provenance line that can be tapped for full detail (the `NodeResult` from the DAG). This is how agents and humans debug values.

---

## Cross-Screen Navigation

```
                         ┌──────────────┐
                         │  User Config │
                         └──────┬───────┘
                                │ settings gear
               ┌────────────────┴────────────────┐
               │          Property List           │
               │        (default landing)         │
               └────────────────┬────────────────┘
                                │ tap card
               ┌────────────────┴────────────────┐
               │         Property Detail          │
               │   (5 expandable sections)         │
               └────────────────┬────────────────┘
                                │ tap expand / ⓘ
               ┌────────────────┴────────────────┐
               │        Group Drill-Down          │
               │   (provenance overlay, DAG)      │
               └─────────────────────────────────┘
               
               ┌─────────────────────────────────┐
               │          Add Property            │
               │   (from ＋ in header)            │
               └─────────────────────────────────┘
```
