# Adding a New Enrichment Module

This guide walks through the steps to add a new enrichment module to the Houses pipeline. Each module follows a consistent pattern.

## 1. Create the Module File

Add a new file in `houses/` (e.g. `houses/my_module.py`). The module should:

- **Accept the minimum input needed** — typically a postcode, address, or coordinates. Avoid passing full property objects.
- **Return structured data** — use an `Attempt[T]` for failures or a simple dataclass/dict. Prefer `Attempt` when failures need explanations.
- **Fail gracefully** — log warnings on API failures, return `None`/empty/`Attempt.impossible()` instead of raising.
- **Be independently callable** — the module should work when invoked standalone, not depend on enricher.py state.

## 2. Add a Pydantic Model

If your module returns data that doesn't fit an existing model, add a dataclass or Pydantic model to `houses/property.py` (e.g. `MyModuleInfo`). Keep it focused — one dataclass per module output.

## 3. Wire Into the Enricher

In `houses/enricher.py`:

1. Call your module from `enrich_property()` (or the appropriate enrichment method).
2. Store the result on `EnrichedProperty` — add a field for it.
3. If your data has multiple related fields (e.g. band + cost), add the `MyModuleInfo` field rather than multiple scalar fields.

## 4. Add Sheet Columns

In `houses/sheets.py`:

1. Add column headers to `COLUMN_HEADERS` in the correct positional order.
2. Add `_fmt_*` helpers if your data needs formatting (currency, distance, etc.).
3. Add entries to `row_values()` mapping your module's output to the correct column headers.

## 5. Add View Formulas (If Needed)

If the column should appear in the Properties View tab:

1. Add a View header to `VIEW_HEADERS`.
2. Add an XLOOKUP formula to `VIEW_FORMULA_COLS`.

## 6. Wire Into the API

If your module should be refreshable independently:

1. Add a field name to the `FIELDS` mapping in `houses/server.py` (used by `POST /properties?fields=...`).
2. Ensure the compare endpoint (`POST /properties/compare`) picks up the new fields.

## 7. Document the Module

Create a `docs/<module-name>.md` following the same structure:

- Module file location and key function(s)
- Data sources and API endpoints
- Key function signatures and parameters
- Which sheet columns are populated
- Graceful degradation behaviour

Add a one-line entry in `docs/enrichment-modules.md` linking to the new doc.

## 8. Write Tests Following Test-First

See `docs/development.md` for test structure and `docs/coding-standards.md` for naming conventions. Write the test before writing the implementation.
