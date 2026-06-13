"""View and Data tab formula definitions — single source of truth for spreadsheet formulas.

``VIEW_FORMULA_COLS`` and ``DATA_FORMULA_COLS`` define every server-managed formula.
``sync_data_formulas`` writes Data tab formula columns.
"""

from __future__ import annotations

import gspread

from houses.sheets.named_ranges import named_range_name

_nr = named_range_name

# ── View tab header definitions ─────────────────────────────────────────

# Canonical View tab headers — single source of truth. Must be imported by
# scripts/setup_sheet.py and tests/integration/test_view_formulas.py.
VIEW_HEADERS: list[str] = [
    "Listing Address",  # A  (0)
    "Rightmove Link",  # B  (1)
    "Map",  # C  (2)
    "Rightmove ID",  # D  (3)
    "Purchase Cost (£)",  # D  (3)
    "EPC Rating",  # E  (4)
    "",  # F  (5)  gap column
    "Simon London",  # G  (6)
    "Simon London Route",  # H  (7)
    "Lorena London",  # I  (8)
    "Lorena London Route",  # J  (9)
    "Bracknell Time",  # K  (10)
    "What the Area is Like",  # L  (11)
    "Walk to Town",  # M  (12)
    "Walkable Amenities",  # N  (13)
    "",  # O  (14) gap column
    "Primary School",  # P  (15)
    "Primary Walk",  # Q  (16)
    "Primary Ofsted",  # R  (17)
    "Primary Inspection Year",  # S  (18)
    "Secondary School",  # T  (19)
    "Secondary Walk",  # U  (20)
    "Secondary Ofsted",  # V  (21)
    "Secondary Inspection Year",  # W  (22)
    "Secondary Bus",  # X  (23)
    "Secondary Bus Route",  # Y  (24)
    "",  # Z  (25) gap column
    "Monthly Mortgage Payment (£)",  # AA (26)
    "Monthly Sinking Fund (£)",  # AB (27)
    "Monthly Life Insurance (£)",  # AC (28)
    "Monthly Commute Cost (£)",  # AD (29)
    "Monthly Council Tax (£)",  # AE (30)
    "Total Monthly Housing Cost (£)",  # AF (31)
    "",  # AG (32) gap column
    "Ashby Works Estimate (£)",  # AH (33)
    "Group Notes / WhatsApp",  # AI (34)
    "Ashby comments",  # AJ (35)
    "Design Needed",  # AK (36) — yes/no dropdown
    "Planning Needed",  # AL (37) — yes/no/yikes dropdown
    "Status",  # AM (38)
    "Status Reason",  # AN (39)
]

# View tab columns that are manual (user-entered), never written by formulas
VIEW_MANUAL_COLUMNS: frozenset[str] = frozenset(
    {
        "",
        "Rightmove Link",
        "Ashby Works Estimate (£)",
        "Group Notes / WhatsApp",
        "Ashby comments",
        "Design Needed",
        "Planning Needed",
        "Status",
        "Status Reason",
    }
)


# ── View tab formula columns ────────────────────────────────────────────

VIEW_FORMULA_COLS: dict[str, str] = {
    "listing address": f"=IFNA(INDEX({_nr('Address')},ROW()),)",
    "map": f'=LET(url,IFNA(INDEX({_nr("Map URL")},ROW()),),IF(url="","",HYPERLINK(url,"Map")))',
    "rightmove id": f"=IFNA(INDEX({_nr('Rightmove ID')},ROW()),)",
    "purchase cost (£)": f"=IFNA(INDEX({_nr('Price (£)')},ROW()),)",
    "epc rating": f"=IFNA(INDEX({_nr('EPC Rating')},ROW()),)",
    "simon london": f'=IFNA(LET(v,IFNA(INDEX({_nr("Simon London (min)")},ROW()),),IF(v="","",IF(v*1=0,"",v/1440))),)',
    "simon london route": f"=IFNA(INDEX({_nr('Simon London Route')},ROW()),)",
    "lorena london": f'=IFNA(LET(v,IFNA(INDEX({_nr("Lorena London (min)")},ROW()),),IF(v="","",IF(v*1=0,"",v/1440))),)',
    "lorena london route": f"=IFNA(INDEX({_nr('Lorena London Route')},ROW()),)",
    "bracknell time": f'=IFNA(LET(v,IFNA(INDEX({_nr("Bracknell Time (min)")},ROW()),),IF(v="","",IF(v*1=0,"",v/1440))),)',
    "what the area is like": f"=IFNA(INDEX({_nr('Area Description')},ROW()),)",
    "walk to town": f'=IFNA(LET(v,IFNA(INDEX({_nr("Walk to Town (min)")},ROW()),),IF(v="","",IF(v*1=0,"",v/1440))),)',
    "walkable amenities": f"=IFNA(INDEX({_nr('Walkable Amenities')},ROW()),)",
    "primary school": f"=HYPERLINK(IFNA(INDEX({_nr('Primary School Link')},ROW()),),IFNA(INDEX({_nr('Primary School')},ROW()),))",
    "primary ofsted": f"=IFNA(INDEX({_nr('Primary Ofsted')},ROW()),)",
    "primary walk": f'=IFNA(LET(v,IFNA(INDEX({_nr("Primary Walk (min)")},ROW()),),IF(v="","",IF(v*1=0,"",v/1440))),)',
    "secondary school": f"=HYPERLINK(IFNA(INDEX({_nr('Secondary School Link')},ROW()),),IFNA(INDEX({_nr('Secondary School')},ROW()),))",
    "secondary ofsted": f"=IFNA(INDEX({_nr('Secondary Ofsted')},ROW()),)",
    "secondary walk": f'=IFNA(LET(v,IFNA(INDEX({_nr("Secondary Walk (min)")},ROW()),),IF(v="","",IF(v*1=0,"",v/1440))),)',
    "secondary bus route": f"=IFNA(INDEX({_nr('Secondary Bus Route')},ROW()),)",
    "secondary bus": f'=IFNA(LET(v,IFNA(INDEX({_nr("Secondary Bus (min)")},ROW()),),IF(v="","",IF(v*1=0,"",v/1440))),)',
    "primary inspection year": f"=IFNA(INDEX({_nr('Primary Inspection Year')},ROW()),)",
    "secondary inspection year": f"=IFNA(INDEX({_nr('Secondary Inspection Year')},ROW()),)",
    # Affordability block — monthly costs only
    "monthly mortgage payment (£)": f"=IFNA(INDEX({_nr('Monthly Mortgage Payment (£)')},ROW()),)",
    "monthly sinking fund (£)": f"=IFNA(INDEX({_nr('Yearly Sinking Fund (£)')},ROW())/12*2/3,)",
    "monthly life insurance (£)": "=IFNA(Const_LifeInsuranceMonthly,)",
    "monthly commute cost (£)": f'=IFNA(LET(k,IFNA(INDEX({_nr("Bracknell Cost (£)")},ROW()),),g,IFNA(INDEX({_nr("Simon London Cost (£)")},ROW()),),i,IFNA(INDEX({_nr("Lorena London Cost (£)")},ROW()),),IF(OR(k="",g="",i=""),"",46*(k+g+2*i)/12)),)',
    "monthly council tax (£)": f'=IFNA(LET(v,IFNA(INDEX({_nr("Council Tax Cost (£)")},ROW()),),IF(v=0,"",v/12)),)',
    "total monthly housing cost (£)": f'=IFNA(LET(mp,IFNA(INDEX({_nr("Monthly Mortgage Payment (£)")},ROW()),),sf,IFNA(INDEX({_nr("Yearly Sinking Fund (£)")},ROW())/12*2/3,),li,Const_LifeInsuranceMonthly,ct,IFNA(LET(v,IFNA(INDEX({_nr("Council Tax Cost (£)")},ROW()),),IF(v=0,"",v/12)),),comm,IFNA(LET(k,IFNA(INDEX({_nr("Bracknell Cost (£)")},ROW()),),g,IFNA(INDEX({_nr("Simon London Cost (£)")},ROW()),),i,IFNA(INDEX({_nr("Lorena London Cost (£)")},ROW()),),IF(OR(k="",g="",i=""),"",46*(k+g+2*i)/12)),),s,IFNA(INDEX(View_Status,ROW()),),gross,IF(OR(mp="",comm="",ct=""),"",mp+IF(s="Current",0,sf)+IF(s="Current",0,li)+comm+ct),p,IF(gross="","",gross-IF(s="Current",IFNA(Const_RentalIncome,0),0)),IF(OR(p="",p=0),"",p)),)',
}

# Data tab formula columns (lowercase header -> Google Sheets formula string).
# These are never written by the server — they're formula-driven.
DATA_FORMULA_COLS: dict[str, str] = {
    "stamp duty (£)": f'=IFNA(LET(s,IFNA(INDEX(View_Status,ROW()),),p,INDEX({_nr("Price (£)")},ROW()),sd,IF(p<=250000,0,IF(p<=925000,(p-250000)*0.05,IF(p<=1500000,(p-925000)*0.1+33750,(p-1500000)*0.12+91250))),IF(s="Current",0,sd)),)',
    "net ashby contribution (£)": f'=IFNA(LET(s,IFNA(INDEX(View_Status,ROW()),),p,INDEX({_nr("Price (£)")},ROW()),na,Const_GrossAshbyContribution-IFNA(INDEX({_nr("Stamp Duty (£)")},ROW())/3,)-IFNA(INDEX(View_AshbyWorksEstimate,ROW()),),IF(s="Current",0,IF(OR(p=0,p=""),na,MIN(na,p/3)))),)',
    "mortgage required (£)": f"=IFNA(INDEX({_nr('Price (£)')},ROW()),)-Const_Deposit-IFNA(INDEX({_nr('Net Ashby Contribution (£)')},ROW()),)",
    "monthly mortgage payment (£)": f'=IFNA(IF(AND(INDEX(View_AshbyWorksEstimate,ROW())="",INDEX(View_Status,ROW())<>"Current"),,PMT(Const_MortgageRate/12,Const_MortgageTermYears*12,-IFNA(INDEX({_nr("Mortgage Required (£)")},ROW()),0))),)',
    "yearly sinking fund (£)": f"=IFNA(INDEX({_nr('Price (£)')},ROW())*Const_SinkingFundRate,)",
    "best latitude": f'=IFNA(LET(a,IFNA(INDEX({_nr("Actual Latitude")},ROW()),),IF(a<>"",a,IFNA(INDEX({_nr("Approx Latitude (est)")},ROW()),))),)',
    "best longitude": f'=IFNA(LET(a,IFNA(INDEX({_nr("Actual Longitude")},ROW()),),IF(a<>"",a,IFNA(INDEX({_nr("Approx Longitude (est)")},ROW()),))),)',
    "map url": f'=LET(lat,IFNA(INDEX({_nr("Best Latitude")},ROW()),0),lng,IFNA(INDEX({_nr("Best Longitude")},ROW()),0),IF(OR(lat=0,lng=0),"","https://www.google.com/maps?q="&lat&","&lng&"&t=k"))',
}


# ── Formula sync ────────────────────────────────────────────────────────


def sync_data_formulas(spreadsheet: gspread.Spreadsheet) -> None:
    """Write Data tab formulas for all formula-only columns (rows 2–N)."""
    from houses.sheets.row import DATA_TAB, Row

    ws = spreadsheet.worksheet(DATA_TAB)
    data = ws.get_all_values()
    num_rows = len(data)

    for header_key, formula in DATA_FORMULA_COLS.items():
        for col_idx, header in enumerate(Row.HEADERS):
            if header.lower() == header_key:
                cl = Row.letter_of(col_idx)
                if num_rows > 1:
                    write_rows = max(num_rows - 1, 1)
                    ws.update(
                        values=[[formula] for _ in range(write_rows)],
                        range_name=f"{cl}2:{cl}{1 + write_rows}",
                        value_input_option="USER_ENTERED",
                    )
                break
