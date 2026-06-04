# Column Reference

> The canonical `COLUMN_HEADERS` list in `houses/sheets.py` is the single source of truth for column definitions. View it directly:
> ```bash
> uv run python -c "from houses.sheets import COLUMN_HEADERS; [print(f'{i:3d} {chr(65+i) if i<26 else chr(64+i//26)+chr(65+i%26):3s} {h}') for i,h in enumerate(COLUMN_HEADERS)]"
> ```
> To add or remove a column, edit `sheets.py` first, then run `uv run python scripts/sheet_tool.py refresh-formulas`.

## Properties Data Tab (Bot / Server-Written)

36 columns (A–AJ), packed densely with no gaps. The server writes enriched rows via `sheets.py:_row_values()`.

Key conventions:
- **Primary key**: Rightmove URL (col A). Stable lookup key: Rightmove ID (col H).
- **Monetary values**: floats, no £ prefix. Display formatting is the sheet's job.
- **Missing data**: empty string (never `None`, `0`, or `"N/A"`).
- **User-owned columns** (A–G): server never overwrites these (Rightmove URL, Address, Postcode, Bedrooms, Price, Actual Lat/Lng).

## Properties View Tab (Human / Formula-Driven)

29 columns (A–AC). Formulas use named ranges (`Data_*`) instead of hardcoded column letters,
so they survive column insertions/reorders in the Data tab.

### Formula Reference

**Lookup key**: `$C2` (Rightmove ID, manually entered per row).

| Col | Header | Formula |
|-----|--------|---------|
| D | Purchase Cost (£) | `XLOOKUP($C2, Data_RightmoveID, Data_Price)` |
| E | EPC Rating | `XLOOKUP($C2, Data_RightmoveID, Data_EPC)` |
| F | Yearly Commute Total (£) | `LET(k,XLOOKUP(BracknellCost),g,XLOOKUP(SimonCost),i,XLOOKUP(LorenaCost),46*(k+g+2*i))` |
| H | Simon London (min) | `XLOOKUP(Data_SimonMins) / 1440` |
| I | Lorena London (min) | `XLOOKUP(Data_LorenaMins) / 1440` |
| J | Bracknell Time (min) | `XLOOKUP(Data_BracknellMins) / 1440` |
| K | What the Area is Like | `XLOOKUP(Data_AreaDescription)` |
| L | Walk to Town (min) | `XLOOKUP(Data_WalkToTown) / 1440` |
| M | Walkable Amenities | `XLOOKUP(Data_WalkableAmenities)` |
| N | Primary School | `HYPERLINK(XLOOKUP(Data_PrimaryLink), XLOOKUP(Data_PrimarySchool))` |
| O | Primary Ofsted | `XLOOKUP(Data_PrimaryOfsted)` |
| P | Primary Walk (min) | `XLOOKUP(Data_PrimaryWalk) / 1440` |
| Q | Secondary School | `HYPERLINK(XLOOKUP(Data_SecondaryLink), XLOOKUP(Data_SecondarySchool))` |
| R | Secondary Ofsted | `XLOOKUP(Data_SecondaryOfsted)` |
| S | Secondary Walk (min) | `XLOOKUP(Data_SecondaryWalk) / 1440` |
| T | Secondary Bus Route | `XLOOKUP(Data_SecondaryBusRoute)` |
| U | Secondary Bus (min) | `=LET(v,XLOOKUP($C2, Data_RightmoveID, Data_SecondaryBusMin),IF(v="","",IF(v*1=0,"",v/1440)))` |
| Z | Primary Inspection Year | `XLOOKUP(Data_PrimaryInspYear)` |
| AB | Secondary Inspection Year | `XLOOKUP(Data_SecondaryInspYear)` |
| A, B, C, G, V, W, X, Y, AA, AC | All other cols | Manual |

All XLOOKUP references implicitly start with `$C2, Data_RightmoveID,` for the key lookup.

### Yearly Commute Formula (Col F)

```gsheets
=LET(
  k, XLOOKUP($C2, Data_RightmoveID, Data_BracknellCost),
  g, XLOOKUP($C2, Data_RightmoveID, Data_SimonCost),
  i, XLOOKUP($C2, Data_RightmoveID, Data_LorenaCost),
  IF(OR(k="",g="",i=""),"",46 * (k + g + 2 * i))
)
```

- **46** working weeks × (1×Bracknell + 1×Simon + 2×Lorena) trips per week
- Lorena commutes 2 days/week; Simon and Bracknell are 1 day/week
- Daily PT cost = 2 × single TfL fare, or NR fare + tube continuation

### School Hyperlink Formula (Cols N, Q)

```gsheets
N: =HYPERLINK(XLOOKUP($C2, Data_RightmoveID, Data_PrimaryLink), XLOOKUP($C2, Data_RightmoveID, Data_PrimarySchool))
Q: =HYPERLINK(XLOOKUP($C2, Data_RightmoveID, Data_SecondaryLink), XLOOKUP($C2, Data_RightmoveID, Data_SecondarySchool))
```

Each school's GIAS details URL is `https://get-information-schools.service.gov.uk/Establishments/Establishment/Details/{URN}`.

### Named Ranges

The full list of named ranges is in `sheets.py:NAMED_RANGE_NAMES`. To refresh them after
a column operation:

```bash
uv run python scripts/sheet_tool.py refresh-formulas
```

## Update Process

To add or modify a column:

1. Edit `COLUMN_HEADERS` in `houses/sheets.py`
2. Update `_row_values()` in `houses/sheets.py` to read/write the new field
3. Add a named range entry in `NAMED_RANGE_NAMES` if the View tab needs it
4. Update `scripts/setup_sheet.py` formula generation if adding a new View column
5. Run `uv run python scripts/sheet_tool.py refresh-formulas` to sync named ranges and rewrite View formulas
6. Update tests that assert column count or column values
7. Run `make test` to verify alignment

## Color Coding

The View tab uses Google Sheets conditional formatting to color-code cells. Thresholds and rules are defined in `houses/sheets.py:sync_view_formulas()`. See that function for the canonical list of coloring rules.
