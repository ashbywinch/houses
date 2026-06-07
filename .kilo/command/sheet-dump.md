---
description: Dump contents of a sheet tab to stdout
---
Dump the contents of a sheet tab as a readable table.

Usage: `/sheet-dump [tab=Properties Data] [limit=10]`

Read the tab with `uv run python -c`
```python
from houses.sheets import get_client
from houses.config import settings

gclient = get_client()
sh = gclient.open_by_key(settings.sheet_id)
ws = sh.worksheet("$1" if "$1" else "Properties Data")
rows = ws.get_all_values()
headers = rows[0]
print(f"{ws.title}: {len(rows)} rows (incl header)")
for r in rows[1:]:
    print(r)
```
