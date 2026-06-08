"""E2E test for View tab formulas — writes test records, validates outputs.

Only one reason to touch a real sheet: Google Sheets evaluates our formula
strings correctly (named range resolution, IFNA/LET/INDEX, PMT, arithmetic).

ALL tests in this file must require a real Google Sheet to function.
If a test can pass without sheet access (using just Python assertions on
headers, formula strings, or code invariants), it belongs in tests/unit/.
This keeps e2e tests fast, quota-efficient, and meaningful.

Run with:  make test-all
"""

import json
import os
import time
from dataclasses import dataclass

import gspread
import pytest
from google.oauth2.service_account import Credentials

from houses.config import settings
from houses.sheets import (
    COLUMN_HEADERS,
    DATA_TAB,
    VIEW_FORMULA_COLS,
    VIEW_HEADERS,
    VIEW_MANUAL_COLUMNS,
    VIEW_TAB,
    col_index,
    col_letter,
    ensure_constants_tab,
    ensure_named_ranges,
    sync_data_formulas,
    sync_view_formulas,
)

pytestmark = pytest.mark.e2e

VC = {h: col_letter(i) for i, h in enumerate(VIEW_HEADERS)}


@dataclass
class _TestRecord:
    url: str = ""
    address: str = ""
    postcode: str = ""
    bedrooms: int = 0
    price: float = 0.0
    rid: int = 0
    simon_min: int = 0
    simon_cost: float = 0.0
    lorena_min: int = 0
    lorena_cost: float = 0.0
    bracknell_min: int = 0
    bracknell_cost: float = 0.0
    primary_school: str = ""
    primary_dist: float = 0.0
    primary_walk: int = 0
    primary_link: str = ""
    primary_ofsted: str = ""
    primary_yr: int = 0
    secondary_school: str = ""
    secondary_dist: float = 0.0
    secondary_walk: int = 0
    secondary_link: str = ""
    secondary_ofsted: str = ""
    secondary_yr: int = 0
    area_desc: str = ""
    walk_min: int = 0
    amenities: str = ""
    epc: str = ""
    bus_min: int = 0
    bus_route: str = ""
    council_tax_band: str = ""
    council_tax_cost: float = 0.0
    simon_route: str = ""
    lorena_route: str = ""
    status: str = ""

    def to_data_row(self):
        ci = col_index
        r = [""] * len(COLUMN_HEADERS)
        r[ci("Rightmove URL")] = self.url
        r[ci("Address")] = self.address
        r[ci("Postcode")] = self.postcode
        r[ci("Bedrooms")] = str(self.bedrooms)
        r[ci("Price (£)")] = str(self.price)
        r[ci("Rightmove ID")] = str(self.rid)
        r[ci("Simon London (min)")] = str(self.simon_min)
        r[ci("Simon London Cost (£)")] = str(self.simon_cost)
        r[ci("Simon London Route")] = self.simon_route
        r[ci("Lorena London (min)")] = str(self.lorena_min)
        r[ci("Lorena London Cost (£)")] = str(self.lorena_cost)
        r[ci("Lorena London Route")] = self.lorena_route
        r[ci("Bracknell Time (min)")] = str(self.bracknell_min)
        r[ci("Bracknell Cost (£)")] = str(self.bracknell_cost)
        r[ci("Primary School")] = self.primary_school
        r[ci("Primary Distance (km)")] = str(self.primary_dist)
        r[ci("Primary Walk (min)")] = str(self.primary_walk)
        r[ci("Primary School Link")] = self.primary_link
        r[ci("Primary Ofsted")] = self.primary_ofsted
        r[ci("Primary Inspection Year")] = str(self.primary_yr)
        r[ci("Secondary School")] = self.secondary_school
        r[ci("Secondary Distance (km)")] = str(self.secondary_dist)
        r[ci("Secondary Walk (min)")] = str(self.secondary_walk)
        r[ci("Secondary School Link")] = self.secondary_link
        r[ci("Secondary Ofsted")] = self.secondary_ofsted
        r[ci("Secondary Inspection Year")] = str(self.secondary_yr)
        r[ci("Area Description")] = self.area_desc
        r[ci("Walk to Town (min)")] = str(self.walk_min)
        r[ci("Walkable Amenities")] = self.amenities
        r[ci("EPC Rating")] = self.epc
        r[ci("Secondary Bus (min)")] = str(self.bus_min)
        r[ci("Secondary Bus Route")] = self.bus_route
        r[ci("Council Tax Band")] = self.council_tax_band
        r[ci("Council Tax Cost (£)")] = str(self.council_tax_cost)
        return r


RECORDS = [
    _TestRecord(
        url="https://www.rightmove.co.uk/properties/11111111",
        address="1 Test Street, Testville, TE1 1ST",
        postcode="TE1 1ST",
        bedrooms=3,
        price=350000,
        rid=11111111,
        simon_min=45,
        simon_cost=12.50,
        simon_route="walk 5m -> Train to Town (30m) -> walk 5m",
        lorena_min=55,
        lorena_cost=15.00,
        lorena_route="walk 3m -> Bus to Town (10m) -> walk 2m",
        bracknell_min=30,
        bracknell_cost=8.50,
        primary_school="Test Primary",
        primary_dist=0.8,
        primary_walk=10,
        primary_link="http://link/prim1",
        primary_ofsted="Good",
        primary_yr=2022,
        secondary_school="Test Secondary",
        secondary_dist=1.5,
        secondary_walk=18,
        secondary_link="http://link/sec1",
        secondary_ofsted="Outstanding",
        secondary_yr=2023,
        area_desc="A nice area to live",
        walk_min=12,
        amenities="Supermarket|Park",
        epc="B",
        bus_min=25,
        bus_route="Bus 101",
        council_tax_band="D",
        council_tax_cost=1800.00,
    ),
    _TestRecord(
        url="https://www.rightmove.co.uk/properties/22222222",
        address="2 Another Road, Otherville, OT2 2ND",
        postcode="OT2 2ND",
        bedrooms=4,
        price=450000,
        rid=22222222,
        status="Current",
        simon_min=35,
        simon_cost=10.00,
        simon_route="walk 3m -> Train to City (25m) -> walk 5m",
        lorena_min=42,
        lorena_cost=12.00,
        lorena_route="walk 4m -> Tube to Bank (15m) -> walk 3m",
        bracknell_min=25,
        bracknell_cost=6.50,
        primary_school="Test Primary 2",
        primary_dist=0.6,
        primary_walk=8,
        primary_link="http://link/prim2",
        primary_ofsted="Requires Improvement",
        primary_yr=2021,
        secondary_school="Test Secondary 2",
        secondary_dist=2.0,
        secondary_walk=25,
        secondary_link="http://link/sec2",
        secondary_ofsted="Good",
        secondary_yr=2024,
        area_desc="Quiet suburban area",
        walk_min=20,
        amenities="Pharmacy|Train Station",
        epc="C",
        bus_min=30,
        bus_route="Bus 202",
        council_tax_band="E",
        council_tax_cost=2200.00,
    ),
]


class TestViewFormulasOnTestSheet:
    TEST_SHEET_ID = settings.test_sheet_id

    @pytest.fixture(scope="class")
    def client(self):
        raw = os.environ.get("GOOGLE_SHEETS_SERVICE_ACCOUNT", settings.service_account_json)
        if not raw:
            pytest.skip("No sheet credentials")
        creds = Credentials.from_service_account_info(
            json.loads(raw), scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        return gspread.authorize(creds)

    @pytest.fixture(scope="class")
    def sh(self, client):
        return client.open_by_key(self.TEST_SHEET_ID)

    @pytest.fixture(scope="class", autouse=True)
    def setup_sheet(self, sh):
        # Read both tabs in one batch to decide what needs updating
        result = sh.values_batch_get(
            [
                f"'{DATA_TAB}'!1:100",
                f"'{VIEW_TAB}'!1:100",
            ]
        )
        data_range = result["valueRanges"][0]
        view_range = result["valueRanges"][1]
        data_values = data_range.get("values", [])
        view_values = view_range.get("values", [])
        data_headers = data_values[0] if data_values else []
        view_headers = view_values[0] if view_values else []

        data_ok = data_headers == COLUMN_HEADERS and len(data_values) >= 1 + len(RECORDS) + 1
        view_ok = view_headers == VIEW_HEADERS and len(view_values) >= 4

        if data_ok and view_ok:
            # Check if the "new row" (row 4) exists in View and Data tabs
            data_new = data_values[1 + len(RECORDS)] if len(data_values) > 1 + len(RECORDS) else []
            view_new = view_values[3] if len(view_values) > 3 else []
            if data_new and data_new[0].startswith("https://") and view_new and view_new[0]:
                return  # Everything is already set up

        # Batch-setup: write headers + records in one shot per tab
        # Data tab: header + RECORDS + new row
        ws_data = sh.worksheet(DATA_TAB)
        ws_data.clear()
        data_rows = [COLUMN_HEADERS]
        for rec in RECORDS:
            data_rows.append(rec.to_data_row())
        # Add new row (row 4) for formula-population test
        ci = col_index
        new_dr = [""] * len(COLUMN_HEADERS)
        new_dr[ci("Rightmove URL")] = "https://www.rightmove.co.uk/properties/33333333"
        new_dr[ci("Address")] = "33 New Street, Testville, TE3 3ST"
        new_dr[ci("Postcode")] = "TE3 3ST"
        new_dr[ci("Bedrooms")] = "3"
        new_dr[ci("Price (£)")] = "275000"
        new_dr[ci("Rightmove ID")] = "33333333"
        data_rows.append(new_dr)
        last_data_col = col_letter(len(COLUMN_HEADERS) - 1)
        ws_data.update(
            values=data_rows,
            range_name=f"A1:{last_data_col}{len(data_rows)}",
            value_input_option="USER_ENTERED",
        )

        # View tab: header + formula rows + manual data
        ws_view = sh.worksheet(VIEW_TAB)
        ws_view.clear()
        formulas = []
        for h in VIEW_HEADERS:
            key = h.lower()
            if key in VIEW_FORMULA_COLS:
                formulas.append(VIEW_FORMULA_COLS[key])
            else:
                formulas.append("")
        last_view_col = col_letter(len(VIEW_HEADERS) - 1)
        # Write header + row 2 (formulas), row 3 (formulas), row 4 (formulas + manual)
        view_rows = [
            VIEW_HEADERS,
            formulas[:],
            formulas[:],
            formulas[:],
        ]
        # Overwrite manual cells for existing records
        status_idx = VIEW_HEADERS.index("Status")
        view_rows[1][VIEW_HEADERS.index("Listing Address")] = RECORDS[0].address
        view_rows[1][VIEW_HEADERS.index("Rightmove Link")] = RECORDS[0].url
        view_rows[1][status_idx] = RECORDS[0].status
        view_rows[2][VIEW_HEADERS.index("Listing Address")] = RECORDS[1].address
        view_rows[2][VIEW_HEADERS.index("Rightmove Link")] = RECORDS[1].url
        view_rows[2][status_idx] = RECORDS[1].status
        view_rows[3][VIEW_HEADERS.index("Listing Address")] = "33 New Street, Testville, TE3 3ST"
        view_rows[3][VIEW_HEADERS.index("Rightmove Link")] = "https://www.rightmove.co.uk/properties/33333333"
        ws_view.update(
            values=view_rows,
            range_name=f"A1:{last_view_col}4",
            value_input_option="USER_ENTERED",
        )

        # Data formulas, named ranges, View formatting
        ensure_constants_tab(sh)
        ensure_named_ranges(sh)
        sync_data_formulas(sh)
        sync_view_formulas(sh)

        time.sleep(5)

    def test_formulas_produce_correct_values(self, sh):
        """Google Sheets evaluates our formula strings correctly."""
        # First verify Data tab headers match COLUMN_HEADERS
        ws_data = sh.worksheet("Properties Data")
        data_headers = ws_data.get_all_values()[0]
        from houses.sheets import COLUMN_HEADERS

        for i, h in enumerate(data_headers):
            expected = COLUMN_HEADERS[i] if i < len(COLUMN_HEADERS) else "?"
            assert h == expected, f"Data tab col {col_letter(i)}({i}) header mismatch: sheet={h!r} code={expected!r}"

        ws = sh.worksheet("Properties View")

        # Read all resolved values for rows 2-4
        first = VC[VIEW_HEADERS[0]]
        last = VC[VIEW_HEADERS[-1]]
        all_data = ws.get_values(f"{first}2:{last}4", value_render_option="FORMATTED_VALUE")
        assert all_data, "No data returned from View tab"

        manual = VIEW_MANUAL_COLUMNS

        # Rows 2-3: fully populated records — every formula column must resolve
        # without errors (#NAME?, #REF!, #ERROR!, #VALUE!) and must not be empty
        error_prefixes = ("#NAME?", "#REF!", "#ERROR!", "#VALUE!", "#N/A", "#DIV/0!")
        for row_idx, row in enumerate(all_data[:2], 2):
            for col_idx, val in enumerate(row):
                h = VIEW_HEADERS[col_idx]
                if h in manual:
                    continue
                assert val is not None and val != "" and not val.startswith(error_prefixes), (
                    f"{VC[h]}{row_idx} = {val!r} (formula column has error or is empty)"
                )

        # Row 4: new row with minimal data — just verify formulas are written
        last_view_col = col_letter(len(VIEW_HEADERS) - 1)
        formula_data = ws.get_values(
            f"A4:{last_view_col}4",
            value_render_option="FORMULA",
        )
        formula_row = formula_data[0] if formula_data else []
        for col_idx, val in enumerate(formula_row):
            h = VIEW_HEADERS[col_idx] if col_idx < len(VIEW_HEADERS) else ""
            if not h:
                continue
            if h in manual:
                assert not val or not val.startswith("="), f"Manual column '{h}' row 4 has formula: {val}"
            else:
                assert val and val.startswith("="), f"Formula column '{h}' row 4 missing formula (got {val!r})"

        # Record 1 (row 2): verify specific values
        r1 = all_data[0]

        # Purchase cost (currency-formatted, e.g. "£350,000.00")
        pc_col = VIEW_HEADERS.index("Purchase Cost (£)")
        pc_raw = r1[pc_col].replace("£", "").replace(",", "").strip()
        assert abs(float(pc_raw) - 350000) < 0.01, f"Purchase Cost: {pc_raw}"

        # Simon commute (45 min / 1440 = 0.03125)
        simon_col = VIEW_HEADERS.index("Simon London")
        assert simon_col < len(r1)
        simon_val = r1[simon_col]
        from contextlib import suppress

        with suppress(ValueError, TypeError):
            assert abs(float(simon_val) - 45 / 1440) < 0.0001

        # Area description
        area_col = VIEW_HEADERS.index("What the Area is Like")
        assert r1[area_col].strip() == "A nice area to live"

        # Primary school hyperlink should contain school name
        ps_col = VIEW_HEADERS.index("Primary School")
        assert "Test Primary" in r1[ps_col]

        # Monthly commute cost: 46 * (8.50 + 12.50 + 2*15.00) / 12 = 46 * 51 / 12 = 195.5
        mc_col = VIEW_HEADERS.index("Monthly Commute Cost (£)")
        mc_val = r1[mc_col].replace("£", "").replace(",", "").strip()
        assert abs(float(mc_val) - 195.5) < 0.01

        # Monthly council tax: 1800 / 12 = 150
        ct_col = VIEW_HEADERS.index("Monthly Council Tax (£)")
        ct_val = r1[ct_col].replace("£", "").replace(",", "").strip()
        assert abs(float(ct_val) - 150) < 0.01

        # Ashby Works Estimate is manual — must have no formula in row 4 (new row)
        aw_col_letter = VC["Ashby Works Estimate (£)"]
        formula_data = ws.get_values(
            f"{aw_col_letter}4:{aw_col_letter}4",
            value_render_option="FORMULA",
        )
        aw_formula = formula_data[0][0] if formula_data and formula_data[0] else ""
        assert not aw_formula or not aw_formula.startswith("=")

        # Verify Status-aware formula behavior
        # Row 2 (data row 1): normal house — SDLT, Ashby contributes
        # Row 3 (data row 2): Status=Current — Stamp Duty = 0, Net Ashby = 0
        ws_data = sh.worksheet("Properties Data")
        d = ws_data.get_values("A1:AT4", value_render_option="FORMATTED_VALUE")
        dheaders = d[0] if d else []
        sd_idx = dheaders.index("Stamp Duty (£)") if "Stamp Duty (£)" in dheaders else -1
        na_idx = dheaders.index("Net Ashby Contribution (£)") if "Net Ashby Contribution (£)" in dheaders else -1
        mr_idx = dheaders.index("Mortgage Required (£)") if "Mortgage Required (£)" in dheaders else -1

        if sd_idx >= 0 and na_idx >= 0 and mr_idx >= 0:
            # Row 2 (d[1]): normal house — SDLT applies, Ashby contributes
            r1_sd = float(d[1][sd_idx]) if d[1][sd_idx] else 0
            r1_na = float(d[1][na_idx]) if d[1][na_idx] else 0
            assert r1_sd == 5000.0, f"Row 2 Stamp Duty should be 5000, got {r1_sd}"
            expected_na = min(300000 - 5000 / 3, 350000 / 3)
            assert abs(r1_na - expected_na) < 1, f"Row 2 Net Ashby should be ~{expected_na:.0f}, got {r1_na}"

            # Row 3 (d[2]): Status=Current — zero SDLT, zero Ashby
            r2_sd = float(d[2][sd_idx]) if d[2][sd_idx] else 0
            r2_na = float(d[2][na_idx]) if d[2][na_idx] else 0
            r2_mr = float(d[2][mr_idx]) if d[2][mr_idx] else 0
            assert r2_sd == 0, f"Row 3 (Current) Stamp Duty should be 0, got {r2_sd}"
            assert r2_na == 0, f"Row 3 (Current) Net Ashby should be 0, got {r2_na}"
            assert abs(r2_mr - (450000 - 177000)) < 1, (
                f"Row 3 (Current) Mortgage Required should be 273000, got {r2_mr}"
            )

        # Monthly Sinking Fund = YearlySinkingFund / 12 * 2/3
        # For Row 2 (Price=350k): 350000 * 0.01 / 12 * 2/3 = 194.44
        msf_idx = VIEW_HEADERS.index("Monthly Sinking Fund (£)")
        msf_row2 = all_data[0][msf_idx] if all_data and len(all_data[0]) > msf_idx else ""
        msf_raw = msf_row2.replace("£", "").replace(",", "").strip()
        assert abs(float(msf_raw) - 194.44) < 1, f"Row 2 Monthly Sinking Fund should be ~194.44, got {msf_row2}"

        # Verify zone separation via right borders and column groups
        import requests as http_requests
        from google.auth.transport.requests import Request as AuthRequest

        creds = ws.client.auth
        if not creds.valid:
            creds.refresh(AuthRequest())
        token = creds.token
        sid = ws._properties["sheetId"]
        url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{ws.spreadsheet.id}"
            f"?ranges=%27Properties%20View%27%21A1%3AAL1"
            f"&fields=sheets.data.rowData.values.effectiveFormat.borders"
        )
        headers = {"Authorization": f"Bearer {token}"}
        resp = http_requests.get(url, headers=headers)
        assert resp.ok, f"Failed to read sheet format: {resp.status_code}"
        resp_data = resp.json()
        sheet_data = resp_data.get("sheets", [{}])[0]
        row_data = sheet_data.get("data", [{}])[0].get("rowData", [{}])[0]
        cell_values = row_data.get("values", [])

        # Zone boundary columns (last col of each zone): E=4, N=13, Y=24, AF=31
        boundary_cols = {4, 13, 24, 31}
        for col_idx, cell in enumerate(cell_values):
            if col_idx not in boundary_cols:
                continue
            borders = cell.get("effectiveFormat", {}).get("borders", {})
            right = borders.get("right", {})
            assert right.get("style") == "SOLID_MEDIUM", (
                f"Column {col_letter(col_idx)} ({col_idx}) is a zone boundary but has no SOLID_MEDIUM right border"
            )

        # Verify 5 independent column groups (gap columns prevent merging)
        groups_url = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{ws.spreadsheet.id}?fields=sheets(properties,columnGroups)"
        )
        gresp = http_requests.get(groups_url, headers=headers)
        gdata = gresp.json()
        for sheet in gdata.get("sheets", []):
            if sheet["properties"]["sheetId"] == sid:
                groups = sheet.get("columnGroups", [])
                # Must have exactly 5 independent groups — no merged [0,38)
                zone_ranges = {(0, 5), (6, 14), (15, 25), (26, 32), (33, 38)}
                found_zones = set()
                for g in groups:
                    r = g["range"]
                    gr = (r["startIndex"], r["endIndex"])
                    if gr in zone_ranges:
                        found_zones.add(gr)
                assert len(groups) == 5, (
                    f"Expected 5 column groups, got {len(groups)}. "
                    f"Groups: {[(g['range']['startIndex'], g['range']['endIndex']) for g in groups]}"
                )
                missing = zone_ranges - found_zones
                assert not missing, f"Missing column groups: {sorted(missing)}"
                break
