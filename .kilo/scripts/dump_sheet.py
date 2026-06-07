#!/usr/bin/env python3
"""Dump sheet tab contents to stdout. Usage: dump_sheet.py [tab] [columns] [limit]"""
import sys

from houses.config import settings
from houses.sheets import get_client

tab = sys.argv[1] if len(sys.argv) > 1 else "Properties Data"
cols_input = sys.argv[2] if len(sys.argv) > 2 else ""
limit = int(sys.argv[3]) if len(sys.argv) > 3 else 10

gclient = get_client()
sh = gclient.open_by_key(settings.sheet_id)
ws = sh.worksheet(tab)
rows = ws.get_all_values()
headers = rows[0]

if cols_input:
    col_names = [c.strip() for c in cols_input.split(",")]
    col_indices = [headers.index(c) for c in col_names]
else:
    col_indices = list(range(len(headers)))
    col_names = headers

print(f"\n{ws.title}: {len(rows)-1} rows")
print("  ".join(f"{h:22s}" for h in col_names))
print("  " + "-" * (24 * len(col_names) - 2))
for r in rows[1:limit+1]:
    vals = [r[i][:22] if i < len(r) else "" for i in col_indices]
    if any(v.strip() for v in vals):
        print("  ".join(f"{v:22s}" for v in vals))
