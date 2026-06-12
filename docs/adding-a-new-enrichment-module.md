# Adding a New Enrichment Module

1. Create the module in `houses/` (e.g. `houses/my_module.py`). The module
   should accept the minimum input needed (postcode, address, or
   coordinates) and return structured data using `Attempt[T]` or a simple
   dataclass/dict.
2. Wire it into the enrichment pipeline in `houses/server.py`'s
   `_run_enrichment()` function.
3. Add columns to `COLUMN_HEADERS` in `houses/sheets.py` and update
   `_row_values()` to format the new fields.
4. Run `POST /sync-view-formulas` if the View tab needs new XLOOKUP formulas.
5. Add tests following the existing patterns.

Follow the pattern of existing modules: fail gracefully (log warning,
return None/default on errors), use the shared cache infrastructure, and
add config fields to `houses/config.py` if new API keys or settings are
needed.
