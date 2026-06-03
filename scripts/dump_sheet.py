"""Dump the current state of both sheet tabs to stdout.

Usage: uv run python scripts/dump_sheet.py
"""

import json
import os
import sys

import gspread
from google.oauth2.service_account import Credentials

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from houses.config import settings  # noqa: E402

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def main():
    creds = Credentials.from_service_account_info(
        json.loads(settings.service_account_json), scopes=SCOPES
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(settings.sheet_id)

    for tab_name in ["Properties Data", "Properties View"]:
        ws = sh.worksheet(tab_name)
        data = ws.get_all_values()
        if not data:
            print(f"\n=== {tab_name}: empty ===")
            continue

        headers = data[0]
        print(f"\n=== {tab_name}: {len(data)-1} rows, {len(headers)} cols ===")
        for i, h in enumerate(headers):
            print(f"  [{i:2d}] {h}")

        for row in data[1:]:
            url = row[0][:40] if row else ""
            filled = sum(1 for v in row if v.strip())
            missing = [
                f"[{i}]{headers[i]}"
                for i, v in enumerate(row)
                if not v.strip()
            ]
            print(f"\n  {url:40s} {filled}/{len(headers)} filled")
            if missing:
                for m in missing[:10]:
                    print(f"    missing: {m}")
            # Show key fields
            kv = {}
            for i, v in enumerate(row):
                if v.strip():
                    kv[headers[i]] = v.strip()[:40]
            if kv:
                for k, v in list(kv.items())[:8]:
                    print(f"    {k}: {v}")


if __name__ == "__main__":
    main()
