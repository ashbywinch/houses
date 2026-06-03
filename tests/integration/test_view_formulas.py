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
from houses.sheets import COLUMN_HEADERS, VIEW_HEADERS, col_index, col_letter

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
        return r


RECORDS = [
    _TestRecord(
        url="https://www.rightmove.co.uk/properties/11111111",
        address="1 Test Street, Testville, TE1 1ST", postcode="TE1 1ST",
        bedrooms=3, price=350000, rid=11111111,
        simon_min=45, simon_cost=12.50, lorena_min=55, lorena_cost=15.00,
        bracknell_min=30, bracknell_cost=8.50,
        primary_school="Test Primary", primary_dist=0.8, primary_walk=10,
        primary_link="http://link/prim1", primary_ofsted="Good", primary_yr=2022,
        secondary_school="Test Secondary", secondary_dist=1.5, secondary_walk=18,
        secondary_link="http://link/sec1", secondary_ofsted="Outstanding", secondary_yr=2023,
        area_desc="A nice area to live", walk_min=12, amenities="Supermarket|Park",
        epc="B", bus_min=25, bus_route="Bus 101",
    ),
    _TestRecord(
        url="https://www.rightmove.co.uk/properties/22222222",
        address="2 Another Road, Otherville, OT2 2ND", postcode="OT2 2ND",
        bedrooms=4, price=450000, rid=22222222,
        simon_min=35, simon_cost=10.00, lorena_min=42, lorena_cost=12.00,
        bracknell_min=25, bracknell_cost=6.50,
        primary_school="Test Primary 2", primary_dist=0.6, primary_walk=8,
        primary_link="http://link/prim2", primary_ofsted="Requires Improvement", primary_yr=2021,
        secondary_school="Test Secondary 2", secondary_dist=2.0, secondary_walk=25,
        secondary_link="http://link/sec2", secondary_ofsted="Good", secondary_yr=2024,
        area_desc="Quiet suburban area", walk_min=20, amenities="Pharmacy|Train Station",
        epc="C", bus_min=30, bus_route="Bus 202",
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
        creds = Credentials.from_service_account_info(json.loads(raw), scopes=["https://www.googleapis.com/auth/spreadsheets"])
        return gspread.authorize(creds)

    @pytest.fixture(scope="class")
    def sh(self, client):
        return client.open_by_key(self.TEST_SHEET_ID)

    @pytest.fixture(scope="class", autouse=True)
    def setup_sheet(self, sh):
        DATA_TAB = "Properties Data"
        VIEW_TAB = "Properties View"

        existing = {ws.title: ws for ws in sh.worksheets()}

        if DATA_TAB in existing:
            ws_data = existing[DATA_TAB]
            ws_data.clear()
            ws_data.resize(rows=100, cols=len(COLUMN_HEADERS))
        else:
            ws_data = sh.add_worksheet(title=DATA_TAB, rows=100, cols=len(COLUMN_HEADERS))
        ws_data.append_row(COLUMN_HEADERS, value_input_option="USER_ENTERED")
        for rec in RECORDS:
            ws_data.append_row(rec.to_data_row(), value_input_option="USER_ENTERED")

        if VIEW_TAB in existing:
            ws_view = existing[VIEW_TAB]
            ws_view.clear()
            ws_view.resize(rows=100, cols=len(VIEW_HEADERS))
        else:
            ws_view = sh.add_worksheet(title=VIEW_TAB, rows=100, cols=len(VIEW_HEADERS))
        ws_view.append_row(VIEW_HEADERS, value_input_option="USER_ENTERED")

        # Build formulas using header-to-column lookups throughout
        V = _view_ref
        D = _data_ref
        RID = "Rightmove ID"
        ADDR = "Listing Address"

        formulas = [
            "",  # Listing Address (manual)
            "",  # Rightmove Link (manual)
            f'=IFNA(REGEXEXTRACT(GETURL("B"&ROW()),"properties/(\\d+)"),XLOOKUP({V(ADDR)},{D("Address")},{D(RID)}))',
            f'=XLOOKUP(VALUE({V(RID)}),{D(RID)},{D("Price (£)")})',
            f'=XLOOKUP(VALUE({V(RID)}),{D(RID)},{D("EPC Rating")})',
            f'=LET(k,XLOOKUP(VALUE({V(RID)}),{D(RID)},{D("Bracknell Cost (£)")}),g,XLOOKUP(VALUE({V(RID)}),{D(RID)},{D("Simon London Cost (£)")}),i,XLOOKUP(VALUE({V(RID)}),{D(RID)},{D("Lorena London Cost (£)")}),IF(OR(k="",g="",i=""),"",46*(k+g+2*i)))',
            "",
            f'=LET(v,XLOOKUP(VALUE({V(RID)}),{D(RID)},{D("Simon London (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
            f'=LET(v,XLOOKUP(VALUE({V(RID)}),{D(RID)},{D("Lorena London (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
            f'=LET(v,XLOOKUP(VALUE({V(RID)}),{D(RID)},{D("Bracknell Time (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
            f'=XLOOKUP(VALUE({V(RID)}),{D(RID)},{D("Area Description")})',
            f'=LET(v,XLOOKUP(VALUE({V(RID)}),{D(RID)},{D("Walk to Town (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
            f'=XLOOKUP(VALUE({V(RID)}),{D(RID)},{D("Walkable Amenities")})',
            f'=HYPERLINK(XLOOKUP(VALUE({V(RID)}),{D(RID)},{D("Primary School Link")}),XLOOKUP(VALUE({V(RID)}),{D(RID)},{D("Primary School")}))',
            f'=XLOOKUP(VALUE({V(RID)}),{D(RID)},{D("Primary Ofsted")})',
            f'=LET(v,XLOOKUP(VALUE({V(RID)}),{D(RID)},{D("Primary Walk (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
            f'=HYPERLINK(XLOOKUP(VALUE({V(RID)}),{D(RID)},{D("Secondary School Link")}),XLOOKUP(VALUE({V(RID)}),{D(RID)},{D("Secondary School")}))',
            f'=XLOOKUP(VALUE({V(RID)}),{D(RID)},{D("Secondary Ofsted")})',
            f'=LET(v,XLOOKUP(VALUE({V(RID)}),{D(RID)},{D("Secondary Walk (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
            f'=XLOOKUP(VALUE({V(RID)}),{D(RID)},{D("Secondary Bus Route")})',
            f'=LET(v,XLOOKUP(VALUE({V(RID)}),{D(RID)},{D("Secondary Bus (min)")}),IF(v="","",IF(v*1=0,"",v/1440)))',
            "",  # Group Notes / WhatsApp
            "",  # Ashby comments
            "",  # Status
            "",  # Status Reason
            f'=XLOOKUP(VALUE({V(RID)}),{D(RID)},{D("Primary Inspection Year")})',
            "",  # Primary Inspection Summary
            f'=XLOOKUP(VALUE({V(RID)}),{D(RID)},{D("Secondary Inspection Year")})',
            "",  # Secondary Inspection Summary
        ]
        last_col = col_letter(len(formulas) - 1)
        ws_view.update(range_name=f"A2:{last_col}3", values=[formulas, formulas], value_input_option="USER_ENTERED")

        # Write human-entry columns by header lookup
        addr_col = VC["Listing Address"]
        link_col = VC["Rightmove Link"]
        ws_view.update(values=[[RECORDS[0].address, RECORDS[0].url]],
                       range_name=f"{addr_col}2:{link_col}2", value_input_option="USER_ENTERED")
        ws_view.update(values=[[RECORDS[1].address, RECORDS[1].url]],  # bare URL: REGEXEXTRACT path tested
                       range_name=f"{addr_col}3:{link_col}3", value_input_option="USER_ENTERED")
        ws_view.update(values=[[RECORDS[1].address, RECORDS[1].url]],  # title only: IFNA fallback path
                       range_name=f"{addr_col}4:{link_col}4", value_input_option="USER_ENTERED")

        # Apply formatting by header name
        sid = ws_view._properties["sheetId"]
        fmt = []
        for h in ["Simon London", "Lorena London", "Bracknell Time", "Walk to Town", "Primary Walk", "Secondary Walk"]:
            ci = VIEW_HEADERS.index(h)
            fmt.append({"repeatCell": {"range": {"sheetId": sid, "startColumnIndex": ci, "endColumnIndex": ci + 1},
                        "cell": {"userEnteredFormat": {"numberFormat": {"type": "TIME", "pattern": "[h]:mm"}}},
                        "fields": "userEnteredFormat.numberFormat"}})
        for h in ["What the Area is Like", "Walkable Amenities", "Primary School", "Secondary School",
                  "Group Notes / WhatsApp", "Ashby comments"]:
            ci = VIEW_HEADERS.index(h)
            fmt.append({"repeatCell": {"range": {"sheetId": sid, "startColumnIndex": ci, "endColumnIndex": ci + 1},
                        "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
                        "fields": "userEnteredFormat.wrapStrategy"}})
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

        manual = {"Listing Address", "Rightmove Link", "Rightmove ID",
                  "Yearly Council Tax (£)", "Group Notes / WhatsApp",
                  "Ashby comments", "Status", "Status Reason",
                  "Primary Inspection Summary", "Secondary Inspection Summary"}
        bad = []
        for row_idx, row in enumerate(all_data, 2):
            for col_idx, val in enumerate(row):
                h = VIEW_HEADERS[col_idx]
                if h in manual:
                    continue
                if val is None or val == "#N/A" or val == "":
                    bad.append(f"{VC[h]}{row_idx}={val!r}")
        assert not bad, f"Formula columns with missing values: {', '.join(bad)}"