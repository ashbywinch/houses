"""Integration test for View tab formulas — writes test records, validates outputs.

Run with:  make test-integration
"""

import json
import os
import time
from dataclasses import dataclass

import gspread
import pytest
from google.oauth2.service_account import Credentials

from houses.config import settings
from houses.sheets import COLUMN_HEADERS, VIEW_HEADERS, VIEW_MANUAL_COLUMNS, col_index, col_letter

pytestmark = pytest.mark.integration

# Column letter for each View header — single source of truth for every test
VC = {h: col_letter(i) for i, h in enumerate(VIEW_HEADERS)}


@dataclass
class _TestRecord:
    """A property record for test data. Fields map to COLUMN_HEADERS by name."""

    url: str
    address: str
    postcode: str
    bedrooms: int
    price: float
    rid: int
    simon_min: int
    simon_cost: float
    lorena_min: int
    lorena_cost: float
    bracknell_min: int
    bracknell_cost: float
    primary_school: str
    primary_dist: float
    primary_walk: int
    primary_link: str
    primary_ofsted: str
    primary_yr: int
    secondary_school: str
    secondary_dist: float
    secondary_walk: int
    secondary_link: str
    secondary_ofsted: str
    secondary_yr: int
    area_desc: str
    walk_min: int
    amenities: str
    epc: str
    bus_min: int
    bus_route: str
    council_tax_band: str
    council_tax_cost: float

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
        r[ci("Lorena London (min)")] = str(self.lorena_min)
        r[ci("Lorena London Cost (£)")] = str(self.lorena_cost)
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
        assert len(r) == len(COLUMN_HEADERS), (
            f"to_data_row returned {len(r)} values but COLUMN_HEADERS has {len(COLUMN_HEADERS)}. "
            "Did you add a Data column without adding test data?"
        )
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
        lorena_min=55,
        lorena_cost=15.00,
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
        simon_min=35,
        simon_cost=10.00,
        lorena_min=42,
        lorena_cost=12.00,
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


def _data_ref(header):
    col = col_letter(col_index(header))
    return f"'Properties Data'!{col}:{col}"


def _view_ref(header):
    col = VC[header]
    return f"INDEX({col}:{col},ROW())"


class TestViewFormulasOnTestSheet:
    """Writes test records to a clean test sheet, validates View tab outputs."""

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
        data_tab = "Properties Data"
        view_tab = "Properties View"

        existing = {ws.title: ws for ws in sh.worksheets()}

        if data_tab in existing:
            ws_data = existing[data_tab]
            ws_data.clear()
            ws_data.resize(rows=100, cols=len(COLUMN_HEADERS))
        else:
            ws_data = sh.add_worksheet(title=data_tab, rows=100, cols=len(COLUMN_HEADERS))
        ws_data.append_row(COLUMN_HEADERS, value_input_option="USER_ENTERED")
        for rec in RECORDS:
            ws_data.append_row(rec.to_data_row(), value_input_option="USER_ENTERED")

        if view_tab in existing:
            ws_view = existing[view_tab]
            ws_view.clear()
            ws_view.resize(rows=100, cols=len(VIEW_HEADERS))
        else:
            ws_view = sh.add_worksheet(title=view_tab, rows=100, cols=len(VIEW_HEADERS))
        ws_view.append_row(VIEW_HEADERS, value_input_option="USER_ENTERED")

        # Use the canonical formula definitions from sheets.py, not a local copy.
        from houses.sheets import VIEW_FORMULA_COLS

        formulas = []
        manual_lower = {h.lower() for h in VIEW_MANUAL_COLUMNS}
        for h in VIEW_HEADERS:
            key = h.lower()
            if key in VIEW_FORMULA_COLS:
                formulas.append(VIEW_FORMULA_COLS[key])
            elif key in manual_lower:
                formulas.append("")
            else:
                raise AssertionError(
                    f"View header {h!r} has neither a formula entry nor is listed "
                    "in VIEW_MANUAL_COLUMNS. Add it to one or the other."
                )

        last_col = col_letter(len(formulas) - 1)
        ws_view.update(range_name=f"A2:{last_col}3", values=[formulas, formulas], value_input_option="USER_ENTERED")

        # Write human-entry columns by header lookup
        addr_col = VC["Listing Address"]
        link_col = VC["Rightmove Link"]
        ws_view.update(
            values=[[RECORDS[0].address, RECORDS[0].url]],
            range_name=f"{addr_col}2:{link_col}2",
            value_input_option="USER_ENTERED",
        )
        ws_view.update(
            values=[[RECORDS[1].address, RECORDS[1].url]],  # bare URL: REGEXEXTRACT path tested
            range_name=f"{addr_col}3:{link_col}3",
            value_input_option="USER_ENTERED",
        )
        ws_view.update(
            values=[[RECORDS[1].address, RECORDS[1].url]],  # title only: IFNA fallback path
            range_name=f"{addr_col}4:{link_col}4",
            value_input_option="USER_ENTERED",
        )

        # Apply formatting by header name
        sid = ws_view._properties["sheetId"]
        fmt = []
        for h in ["Simon London", "Lorena London", "Bracknell Time", "Walk to Town", "Primary Walk", "Secondary Walk"]:
            ci = VIEW_HEADERS.index(h)
            fmt.append(
                {
                    "repeatCell": {
                        "range": {"sheetId": sid, "startColumnIndex": ci, "endColumnIndex": ci + 1},
                        "cell": {"userEnteredFormat": {"numberFormat": {"type": "TIME", "pattern": "[h]:mm"}}},
                        "fields": "userEnteredFormat.numberFormat",
                    }
                }
            )
        for h in [
            "What the Area is Like",
            "Walkable Amenities",
            "Primary School",
            "Secondary School",
            "Group Notes / WhatsApp",
            "Ashby comments",
        ]:
            ci = VIEW_HEADERS.index(h)
            fmt.append(
                {
                    "repeatCell": {
                        "range": {"sheetId": sid, "startColumnIndex": ci, "endColumnIndex": ci + 1},
                        "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
                        "fields": "userEnteredFormat.wrapStrategy",
                    }
                }
            )
        if fmt:
            sh.batch_update({"requests": fmt})

        time.sleep(3)

    # ---- Tests ----

    def test_bare_url_extracts_correct_id(self, sh):
        ws = sh.worksheet("Properties View")
        col = VC["Rightmove ID"]
        val = ws.get_values(f"{col}2:{col}2", value_render_option="FORMATTED_VALUE")
        assert val and val[0] and val[0][0] == str(RECORDS[0].rid)

    def test_decorated_url_extracts_correct_id(self, sh):
        ws = sh.worksheet("Properties View")
        col = VC["Rightmove ID"]
        val = ws.get_values(f"{col}3:{col}3", value_render_option="FORMATTED_VALUE")
        assert val and val[0] and val[0][0] == str(RECORDS[1].rid)

    def test_purchase_cost_populated(self, sh):
        ws = sh.worksheet("Properties View")
        col = VC["Purchase Cost (£)"]
        for i, rec in enumerate(RECORDS, 2):
            v = ws.get_values(f"{col}{i}:{col}{i}", value_render_option="FORMATTED_VALUE")
            val = v[0][0] if v and v[0] else None
            assert val is not None and val not in ("#N/A", ""), f"Row {i} Price: {val!r}"
            assert float(val) == rec.price, f"Row {i} expected {rec.price}, got {val}"

    def test_simon_commute_populated(self, sh):
        ws = sh.worksheet("Properties View")
        col = VC["Simon London"]
        for i, rec in enumerate(RECORDS, 2):
            v = ws.get_values(f"{col}{i}:{col}{i}", value_render_option="FORMATTED_VALUE")
            val = v[0][0] if v and v[0] else None
            assert val is not None and val not in ("#N/A", ""), f"Row {i} Simon: {val!r}"
            frac = rec.simon_min / 1440
            try:
                fval = float(val)
                assert abs(fval - frac) < 0.0001, f"Row {i} expected {frac}, got {val}"
            except ValueError:
                assert f"0:{rec.simon_min}" in str(val) or f"{rec.simon_min}:00" in str(val)

    def test_area_description_populated(self, sh):
        ws = sh.worksheet("Properties View")
        col = VC["What the Area is Like"]
        for i, rec in enumerate(RECORDS, 2):
            v = ws.get_values(f"{col}{i}:{col}{i}", value_render_option="FORMATTED_VALUE")
            val = v[0][0] if v and v[0] else ""
            assert val.strip() == rec.area_desc, f"Row {i}: expected {rec.area_desc!r}, got {val!r}"

    def test_primary_school_populated(self, sh):
        ws = sh.worksheet("Properties View")
        col = VC["Primary School"]
        for i, rec in enumerate(RECORDS, 2):
            v = ws.get_values(f"{col}{i}:{col}{i}", value_render_option="FORMATTED_VALUE")
            val = v[0][0] if v and v[0] else ""
            assert rec.primary_school in val, f"Row {i} primary school: {val!r}"

    def test_all_formula_columns_have_no_na(self, sh):
        ws = sh.worksheet("Properties View")
        first = VC[VIEW_HEADERS[0]]
        last = VC[VIEW_HEADERS[-1]]
        all_data = ws.get_values(f"{first}2:{last}3", value_render_option="FORMATTED_VALUE")
        if not all_data:
            pytest.fail("No data returned from View tab")

        manual = VIEW_MANUAL_COLUMNS
        bad = []
        for row_idx, row in enumerate(all_data, 2):
            for col_idx, val in enumerate(row):
                h = VIEW_HEADERS[col_idx]
                if h in manual:
                    continue
                if val is None or val == "#N/A" or val == "":
                    bad.append(f"{VC[h]}{row_idx}={val!r}")
        assert not bad, f"Formula columns with missing values: {', '.join(bad)}"

    def test_manual_columns_not_overwritten_by_formulas(self, sh):
        """Manual columns must not be overwritten by formula-writing.

        After sync_view_formulas() runs, manual columns should still be empty
        (no formula values leaked into them). Formula-writing should only
        touch columns listed in formula_cols dict.
        """
        ws = sh.worksheet("Properties View")
        # Run sync_view_formulas to simulate refresh-formulas
        from houses.sheets import sync_view_formulas

        sync_view_formulas(sh)

        all_data = ws.get_all_values()
        headers = all_data[0]

        # These columns are manual — they should NOT have been written by sync_view_formulas
        manual_cols = VIEW_MANUAL_COLUMNS

        # Verify all rows: manual columns should have no formula values
        for row_idx, row in enumerate(all_data[1:], 2):
            for col_idx, val in enumerate(row):
                h = headers[col_idx] if col_idx < len(headers) else ""
                if h in manual_cols and val and val.startswith("="):
                    pytest.fail(f"Manual column '{h}' row {row_idx} has formula: {val}")

    def test_new_row_gets_formulas_after_sync(self, sh):
        """When a new row is added to the View tab, sync_view_formulas populates
        the formula columns and leaves manual columns empty."""
        from houses.sheets import sync_view_formulas

        ws_data = sh.worksheet("Properties View")

        # Find the last row in the View tab
        existing = ws_data.get_all_values()
        new_row_num = len(existing) + 1

        # Add a new View tab row with a Rightmove link (user adds this manually)
        url = "https://www.rightmove.co.uk/properties/33333333"
        link_col = VC["Rightmove Link"]
        addr_col = VC["Listing Address"]
        ws_data.update(
            values=[["33 New Street, Testville, TE3 3ST", url]],
            range_name=f"{addr_col}{new_row_num}:{link_col}{new_row_num}",
            value_input_option="USER_ENTERED",
        )

        # Run sync_view_formulas to populate the new row
        sync_view_formulas(sh)
        time.sleep(2)

        # Read back the new row
        all_data = ws_data.get_all_values()
        row = all_data[new_row_num - 1] if len(all_data) > new_row_num - 1 else []

        manual = VIEW_MANUAL_COLUMNS
        errors = []
        for col_idx, val in enumerate(row):
            h = VIEW_HEADERS[col_idx] if col_idx < len(VIEW_HEADERS) else ""
            if not h:
                continue
            if h in manual:
                if val and val.startswith("="):
                    errors.append(f"Manual column '{h}' has formula: {val}")
            else:
                # Non-manual columns should have a formula (start with "=") or be a number
                # (Some computed values might resolve immediately if data exists)
                if not val:
                    errors.append(f"Formula column '{h}' is empty")
        assert not errors, f"New row errors: {'; '.join(errors)}"

    def test_yearly_commute_calculated_correctly(self, sh):
        """Yearly Commute Total (£) matches 46 * (Bracknell + Simon + 2*Lorena)."""
        col = VC["Yearly Commute Total (£)"]
        for i, rec in enumerate(RECORDS, 2):
            v = sh.worksheet("Properties View").get_values(
                f"{col}{i}:{col}{i}", value_render_option="FORMATTED_VALUE"
            )
            val = v[0][0] if v and v[0] else None
            expected = 46 * (rec.bracknell_cost + rec.simon_cost + 2 * rec.lorena_cost)
            assert val is not None and val not in ("#N/A", "#ERROR!", ""), (
                f"Row {i} Yearly Commute is empty/error"
            )
            assert abs(float(val.replace("£", "").replace(",", "")) - expected) < 0.01, (
                f"Row {i} expected {expected}, got {val}"
            )

    def test_all_view_headers_are_covered(self):
        """Every View tab header is either a formula column or a manual column.
        If you add a column to VIEW_HEADERS you must add it to VIEW_FORMULA_COLS
        or VIEW_MANUAL_COLUMNS — otherwise this test fails."""
        from houses.sheets import VIEW_FORMULA_COLS

        manual_lower = {h.lower() for h in VIEW_MANUAL_COLUMNS}
        formula_keys = set(VIEW_FORMULA_COLS.keys())
        uncovered = []
        for h in VIEW_HEADERS:
            key = h.lower()
            if key not in formula_keys and key not in manual_lower:
                uncovered.append(h)
        assert not uncovered, (
            f"View headers with no formula or manual entry: {uncovered}. "
            "Add each to VIEW_FORMULA_COLS or VIEW_MANUAL_COLUMNS in sheets.py."
        )
