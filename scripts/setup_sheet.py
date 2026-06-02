"""One-time setup: create AI_Data_Source (Bot) tab and setup Properties with XLOOKUP formulas.

Usage: uv run python scripts/setup_sheet.py
"""

import json
import os

import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

BOT_TAB = "AI_Data_Source (Bot)"
HUMAN_TAB = "Properties"

BOT_HEADERS = [
    "Rightmove URL",
    "Address",
    "Postcode",
    "Bedrooms",
    "Price (£)",
    "Simon Commute (min)",
    "Lorenas Commute (min)",
    "Bracknell Petrol (£)",
    "Primary School",
    "Primary School Distance (km)",
    "Secondary School",
    "Secondary School Distance (km)",
]

HUMAN_HEADERS = [
    "Property Name",
    "Rightmove URL",
    "Address",
    "Postcode",
    "Bedrooms",
    "Price (£)",
    "Simon Commute (min)",
    "Lorenas Commute (min)",
    "Bracknell Petrol (£)",
    "Primary School",
    "Secondary School",
    "Status",
    "Comments",
]


def main():
    raw = os.environ["GOOGLE_SHEETS_SERVICE_ACCOUNT"]
    sheet_id = os.environ["HOUSES_SHEET_ID"]

    creds = Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)

    # Create AI_Data_Source (Bot) tab
    existing = {w.title for w in sh.worksheets()}
    if BOT_TAB not in existing:
        ws_bot = sh.add_worksheet(title=BOT_TAB, rows=500, cols=12)
        ws_bot.append_row(BOT_HEADERS, value_input_option="USER_ENTERED")
        print(f"Created '{BOT_TAB}' tab")
    else:
        print(f"'{BOT_TAB}' tab already exists")

    # Setup Properties tab with XLOOKUP formulas
    if HUMAN_TAB not in existing:
        ws_human = sh.add_worksheet(title=HUMAN_TAB, rows=500, cols=12)
        print(f"Created '{HUMAN_TAB}' tab")
    else:
        ws_human = sh.worksheet(HUMAN_TAB)
        print(f"Updating existing '{HUMAN_TAB}' tab")

    ws_human.update(
        range_name="A1:M1",
        values=[HUMAN_HEADERS],
        value_input_option="USER_ENTERED",
    )

    # XLOOKUP formulas for row 2 (template)
    # Column B has the Rightmove URL, which matches column A in AI_Data_Source
    bot = f"'{BOT_TAB}'"
    formulas = [
        "",  # A: Property name (manual)
        "",  # B: Rightmove URL (manual — paste link here)
        f'=IFERROR(XLOOKUP($B2, {bot}!$A:$A, {bot}!$B:$B), "")',   # C: Address
        f'=IFERROR(XLOOKUP($B2, {bot}!$A:$A, {bot}!$C:$C), "")',   # D: Postcode
        f'=IFERROR(XLOOKUP($B2, {bot}!$A:$A, {bot}!$D:$D), "")',   # E: Bedrooms
        f'=IFERROR(XLOOKUP($B2, {bot}!$A:$A, {bot}!$E:$E), "")',   # F: Price
        f'=IFERROR(XLOOKUP($B2, {bot}!$A:$A, {bot}!$F:$F), "")',   # G: Simon
        f'=IFERROR(XLOOKUP($B2, {bot}!$A:$A, {bot}!$G:$G), "")',   # H: Lorena
        f'=IFERROR(XLOOKUP($B2, {bot}!$A:$A, {bot}!$H:$H), "")',   # I: Petrol
        f'=IFERROR(XLOOKUP($B2, {bot}!$A:$A, {bot}!$I:$I), "")',   # J: Primary School
        f'=IFERROR(XLOOKUP($B2, {bot}!$A:$A, {bot}!$K:$K), "")',   # K: Secondary School
        "",  # L: Status (manual)
        "",  # M: Comments (manual)
    ]
    ws_human.update(
        range_name="A2:M2",
        values=[formulas],
        value_input_option="USER_ENTERED",
    )
    print(f"Added XLOOKUP formulas to {HUMAN_TAB}!A2:M2")
    print("Done!")


if __name__ == "__main__":
    main()
