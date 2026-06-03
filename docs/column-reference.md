# Column Reference

> **Single source of truth** for all column definitions. The canonical `COLUMN_HEADERS` list lives in `houses/sheets.py`. If you need to add, remove, or reorder a column, edit `sheets.py` first, then update this document.

## Properties Data Tab (Bot / Server-Written)

Column letters are alphabetical for the current 28-column layout. The server writes rows via `sheets.py:_row_values()`.

| Col | Header | Type | Source / Description |
|-----|--------|------|---------------------|
| A | Rightmove URL | string | Primary key. From payload. |
| B | Address | string | From payload. |
| C | Postcode | string | From payload or extracted from address. |
| D | Bedrooms | int | From payload. |
| E | Price (£) | float | From payload. No £ prefix in raw data. |
| F | Simon London (min) | int | Transit time to SW1V 2QQ (TfL). |
| G | Simon London Cost (£) | float | Daily public transport cost (return trip, 2× single fare). |
| H | Lorena London (min) | int | Transit time to EC3A 7LP (TfL). |
| I | Lorena London Cost (£) | float | Daily public transport cost (return trip, 2× single fare). |
| J | Bracknell Time (min) | int | Drive time to RG12 8YA (ORS, round trip). |
| K | Bracknell Cost (£) | float | Daily petrol cost (ORS distance × mpg × price). |
| L | Primary School | string | Nearest boys-eligible primary school name. |
| M | Primary Distance (km) | float | Haversine distance from property. |
| N | Primary Walk (min) | int | Walking time at 5 km/h. |
| O | Primary School Link | string | GIAS details URL. |
| P | Primary Ofsted | string | Ofsted rating from merged GIAS data. |
| Q | Secondary School | string | Nearest boys-eligible secondary school name. |
| R | Secondary Distance (km) | float | Haversine distance from property. |
| S | Secondary Walk (min) | int | Walking time at 5 km/h. |
| T | Secondary School Link | string | GIAS details URL. |
| U | Secondary Ofsted | string | Ofsted rating from merged GIAS data. |
| V | Area Description | string | LLM-generated description (OpenRouter). |
| W | Walk to Town (min) | int | Walking time to town centre (ORS). |
| X | Walkable Amenities | string | Formatted list: "Supermarket (5m) | Park (10m)". |
| Y | Council Tax Band | string | From Homedata API (deferred — stub only). |
| Z | Council Tax Yearly (£) | float | Band ratio × Band D rate (deferred — stub only). |
| AA | Council Tax Source | string | Evidence URL (CivAccount page) (deferred — stub only). |
| AB | EPC Rating | string | Placeholder — enrichment not yet implemented. |

### Cost Format

All monetary values are stored as **floats** (or empty string when unavailable). No `£` prefix, no commas in raw data. Formatting for display is handled by Google Sheets cell formatting or XLOOKUP formatting in the View tab.

### School Info Structure

Each school entry includes the linked GIAS details page URL:
`https://get-information-schools.service.gov.uk/Establishments/Establishment/Details/{URN}`

## Properties View Tab (Human / Formula-Driven)

Column layout optimized for human readability. Groups: Identity → Financial Summary → Commute Detail → Location → Schools → Commentary.

| Col | Header | Group | Formula |
|-----|--------|-------|---------|
| A | Listing Address | **Identity** | Manual (Rightmove listing title) |
| B | Rightmove Link | **Identity** | Manual (paste URL) |
| C | Purchase Cost (£) | **Financial Summary** | `=XLOOKUP($B2, 'Properties Data'!$A:$A, 'Properties Data'!$E:$E)` |
| D | EPC Rating | **Financial Summary** | `=XLOOKUP($B2, 'Properties Data'!$A:$A, 'Properties Data'!$AB:$AB)` (placeholder) |
| E | Yearly Commute Total (£) | **Financial Summary** | `=46*(XLOOKUP(K)+XLOOKUP(G)+2*XLOOKUP(I))` — see formula below |
| F | Yearly Council Tax (£) | **Financial Summary** | `=XLOOKUP($B2, 'Properties Data'!$A:$A, 'Properties Data'!$Z:$Z)` (placeholder) |
| G | Simon London (min) | **Commute Detail** | `=XLOOKUP($B2, 'Properties Data'!$A:$A, 'Properties Data'!$F:$F)` |
| H | Lorena London (min) | **Commute Detail** | `=XLOOKUP($B2, 'Properties Data'!$A:$A, 'Properties Data'!$H:$H)` |
| I | Bracknell Time (min) | **Commute Detail** | `=XLOOKUP($B2, 'Properties Data'!$A:$A, 'Properties Data'!$J:$J)` |
| J | What the Area is Like | **Location** | `=XLOOKUP($B2, 'Properties Data'!$A:$A, 'Properties Data'!$V:$V)` |
| K | Walk to Town (min) | **Location** | `=XLOOKUP($B2, 'Properties Data'!$A:$A, 'Properties Data'!$W:$W)` |
| L | Walkable Amenities | **Location** | `=XLOOKUP($B2, 'Properties Data'!$A:$A, 'Properties Data'!$X:$X)` |
| M | Primary School | **Schools** | `=HYPERLINK(XLOOKUP(O), XLOOKUP(L))` — clickable name |
| N | Primary Ofsted | **Schools** | `=XLOOKUP($B2, 'Properties Data'!$A:$A, 'Properties Data'!$P:$P)` |
| O | Primary Walk (min) | **Schools** | `=XLOOKUP($B2, 'Properties Data'!$A:$A, 'Properties Data'!$N:$N)` |
| P | Secondary School | **Schools** | `=HYPERLINK(XLOOKUP(T), XLOOKUP(Q))` — clickable name |
| Q | Secondary Ofsted | **Schools** | `=XLOOKUP($B2, 'Properties Data'!$A:$A, 'Properties Data'!$U:$U)` |
| R | Secondary Walk (min) | **Schools** | `=XLOOKUP($B2, 'Properties Data'!$A:$A, 'Properties Data'!$S:$S)` |
| Q | Group Notes / WhatsApp | **Commentary** | Manual |
| R | Ashby comments | **Commentary** | Manual |
| S | Status | **Commentary** | Manual |

### Yearly Commute Formula (Col E)

```
=46 * (XLOOKUP(B2, Data!K:K) + XLOOKUP(B2, Data!G:G) + 2 * XLOOKUP(B2, Data!I:I))
```

Where:
- **Data!K** = Bracknell daily petrol cost (return trip)
- **Data!G** = Simon London daily PT cost (return trip)
- **Data!I** = Lorena London daily PT cost (return trip)
- **46** = working weeks per year
- **2×** = Lorena commutes 2 days/week (Simon and Bracknell are 1 day/week)
- **Daily PT cost** = 2 × single TfL fare (return trip) or estimated at £0.30/min when fare unavailable

### School Hyperlink Formula (Cols M, P)

```
M: =HYPERLINK(XLOOKUP(B2, Data!O:O), XLOOKUP(B2, Data!L:L))
P: =HYPERLINK(XLOOKUP(B2, Data!T:T), XLOOKUP(B2, Data!Q:Q))
```

The school name is a clickable link to the GIAS details page. Ofsted ratings are in separate columns (N, Q).

## Update Process

To add or modify a column:

1. Edit `COLUMN_HEADERS` in `houses/sheets.py`
2. Update `_row_values()` in `houses/sheets.py` to read/write the new field
3. Update this `column-reference.md`
4. Update the XLOOKUP formulas in `scripts/setup_sheet.py`
5. Update any tests that assert column count or column values
6. Run `make test` to verify alignment
