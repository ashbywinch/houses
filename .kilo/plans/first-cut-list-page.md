# First Cut — Property List Page

## Goal

A single HTML page at `GET /` that renders the property list from sheet data using the redesigned card layout. No navigation, no detail pages, no add-property flow. The page shows active properties, the current home, and dismissed properties behind a toggle.

## Scope

**In scope:**
- `GET /` route that renders a full HTML page
- Read sheet data from both Data tab (raw values) and View tab (status, total monthly cost)
- Merge by Rightmove ID
- Compute derived values: commute colours, school dual-colour dots, financial delta vs current home, walk rating
- Render 4 card variants: active (Maybe/unset), current home (green border), unenriched (compact, price as proxy), dismissed (red border, dimmed, hidden behind toggle)
- CSS: mobile-first, colour system, card styles, badges, pills, dots, header
- "Show dismissed" toggle via vanilla JS + CSS class toggle
- Card styles are inert (no tap action yet)

**Explicitly out of scope:**
- Property detail page
- Add property form
- User config
- HTMX integration (first cut is server-rendered HTML + minimal vanilla CSS/JS)
- Sorting, filtering, search
- School dual-dot hover/tap tooltips (just the dots render)
- Enrichment progress UI
- Tests

## Data Flow

```
Data tab (get_properties_data)      View tab (new get_view_data)
        │                                      │
        └─────────── merge by RID ─────────────┘
                            │
                    CardData transformer
                    (compute colours, delta, etc.)
                            │
                    Jinja2 template render
                            │
                    HTML page with 4 card variants
```

### Data tab columns used (from `Row.HEADERS`)

| Column | Field |
|---|---|
| Rightmove ID | Lookup key |
| Address | Card line 1 |
| Price (£) | Card line 1, proxy for cost |
| Bedrooms | Card line 2 |
| Postcode | Extract district (e.g. SW1) |
| Simon London (min) | Commute pill |
| Simon London Cost (£) | |
| Lorena London (min) | Commute pill |
| Lorena London Cost (£) | |
| Bracknell Time (min) | Commute pill |
| Bracknell Cost (£) | |
| Primary School | School line |
| Primary Distance (km) | |
| Primary Walk (min) | School walk dot |
| Primary Ofsted | School Ofsted dot |
| Primary Inspection Year | Tooltip content |
| Secondary School | School line |
| Secondary Walk (min) | School walk dot |
| Secondary Bus (min) | |
| Secondary Ofsted | School Ofsted dot |
| Secondary Inspection Year | Tooltip content |
| Walk to Town (min) | Financial line badge |
| Walkable Amenities | |

### View tab columns used (from `VIEW_HEADERS`)

| Column | Field |
|---|---|
| Rightmove ID | Lookup key |
| Total Monthly Housing Cost (£) | Financial line |
| Status | Card variant (Maybe/No/Current/unset) |

### CardData dataclass (Python)

```python
@dataclass
class CardData:
    rid: str
    address: str
    price: float | None
    bedrooms: int | None
    postcode_district: str

    simon_minutes: int | None
    simon_cost: float | None
    lorena_minutes: int | None
    lorena_cost: float | None
    bracknell_minutes: int | None
    bracknell_cost: float | None

    primary_name: str
    primary_ofsted: str
    primary_walk_minutes: int | None
    primary_inspection_year: str

    secondary_name: str
    secondary_ofsted: str
    secondary_walk_minutes: int | None
    secondary_bus_minutes: int | None
    secondary_inspection_year: str

    total_monthly_cost: float | None
    walk_to_town_minutes: int | None
    status: str  # "" | "Maybe" | "No" | "Current"
```

### Derived values (computed in Python)

- `commute_colour(minutes, person)` → `"good"|"warn"|"bad"` per threshold rules
- `ofsted_colour(rating)` → `"good"|"warn"|"bad"`
- `walk_colour(minutes)` → `"good"|"warn"|"bad"`
- `total_monthly` (from View tab, or None)
- `current_home_total` (the total for Status=="Current" property)
- `delta` = `card.total_monthly - current_home_total` (if both known)
- `is_enriched` = commute data exists (proxy for enrichment status)

### Card rendering logic

```
if status == "Current" → green left border, 🏠, absolute total, no schools
if status == "No"      → red left border, 50% opacity, hidden behind toggle
if not is_enriched     → compact card (no commute/school lines, price as proxy)
else                   → full 5-line card, Maybe dot or unset dot
```

## Files to Create

### New files

| File | Purpose | ~Lines |
|---|---|---|
| `houses/web/__init__.py` | Package init | 1 |
| `houses/web/router.py` | FastAPI router: configure Jinja2, serve static files, `GET /` | 50 |
| `houses/web/card_data.py` | `get_view_data()`, `CardData` dataclass, `transform_row()` | 100 |
| `houses/templates/base.html` | HTML shell, CSS/JS links, viewport | 20 |
| `houses/templates/property_list.html` | Full page with card loop, 4 variants | 80 |
| `houses/templates/_card.html` | Card component (Jinja2 macro) | 60 |
| `houses/static/css/app.css` | All styles: cards, colours, badges, pills, dots, header, dismissed toggle, responsive | 250 |
| `houses/static/js/app.js` | Toggle dismissed visibility | 10 |

### Modified files

| File | Change | ~Lines |
|---|---|---|
| `houses/server.py` | Import and mount `web.router` at `""` or add `GET /` route → 3 lines | 3 |
| `pyproject.toml` | Add `jinja2` dependency | 1 |

## Implementation Steps

### Step 1: Add jinja2 dependency

Add `"jinja2>=3.1.0"` to `pyproject.toml` dependencies.

### Step 2: Mount web router in server.py

```python
from houses.web.router import web_router
app.include_router(web_router)
```

### Step 3: Create card_data.py

- `get_view_data()` — reads "Properties View" tab, returns `list[dict]` keyed by header
- `CardData` dataclass
- `compute_commute_colour(minutes, person)` — thresholds per person
- `compute_ofsted_colour(rating)` — Outstanding→good, Good→warn, RI/Inadequate→bad
- `compute_walk_colour(minutes)` — 15/30 thresholds
- `compute_card_data(data_tab_row, view_tab_row)` → `CardData`
- `get_all_cards()` → `list[CardData]` — orchestrates read+merge+transform

### Step 4: Create web router

- `Jinja2Templates(directory="houses/templates")`
- Mount static files at `/static/`
- `GET /` → read all cards, find current home total, compute deltas, render

### Step 5: Create templates

- `base.html`: `<!DOCTYPE html>`, viewport meta, link css, link js
- `property_list.html`: header with title + "+" button + "Show dismissed (n)" toggle, card grid, dismissed section
- `_card.html`: Jinja2 macro `render_card(card, current_home_total)` with 5-line layout and state branches

### Step 6: Create CSS

- Colour tokens as CSS custom properties
- Header styles
- Card grid (mobile single-column)
- Card variants (normal, current, dismissed, unenriched)
- 5 card lines with proper spacing
- Commute pills (colour-coded)
- School dual dots
- Financial line
- Walk badge
- Dismissed toggle button
- Responsive: tablet 2-col grid, desktop 3-col grid
- Skeleton loading placeholder
- Empty state

### Step 7: Create minimal JS

- `document.getElementById("dismissed-toggle").onclick` → toggle class on dismissed section

### Step 8: Verify

- `make run` → dev server starts
- Visit `/` → cards render from sheet data
- Check mobile layout (Chrome DevTools 375px)
- Toggle dismissed visibility
- Verify all 4 card states are handled

## Open Questions

1. **Bathrooms**: The Data tab doesn't have a bathroom column. Card line 2 shows "3 bed · 1 bath · SW1" but we don't have bath data from the sheet. Options: drop "bath" from line 2, or leave it in the design for when data is available. → **Decision: drop bathrooms, just show "3 bed · SW1"**

2. **Current home without enrichment**: If the current home hasn't been enriched (no total monthly cost), the delta for other cards won't be computable. → **Decision: show absolute total on current home card, skip delta on other cards if missing**

3. **Empty state**: What renders when there are zero properties in the sheet? → **Decision: "No houses yet" message**

4. **Error state**: What renders when the sheet is unreachable? → **Decision: "Failed to load properties. Check sheet configuration."**
