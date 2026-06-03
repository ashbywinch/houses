# Coding Standards

These standards apply to all code in the houses project. They help readers (human or agent) understand what the code does and why it is structured that way.

## Naming

- **Name things after what they are in the domain**, not after their structural role in the architecture. A function that computes a transit commute should be named `compute_transit`, not `TransitOrchestrator` or `CommuteHandler`.
- **Classes represent things; functions represent actions.** A class name should be a noun from the problem space (`TransitInfo`, `SchoolInfo`, `PetrolCost`). A function name should be a verb (`compute_transit`, `find_nearest_boys_primary`, `write_enriched_row`).
- **Avoid vague suffixes** like "Manager", "Orchestrator", "Handler", "Controller", "Context", "Tools" in class and module names. If the domain concept is clear, the name will be simple.
- **Module names should be domain-driven**, not structural. Use `walkability.py` not `walkability_utils.py`, `town_desc.py` not `town_description_tools.py`.
- A module named "utils" is a grab bag. It has no single responsibility. Name modules after what they do.

## Types

- Use expressive types. Pydantic models are the source of truth for data shapes.
- If tempted to use `Any`, double-check whether a narrower type applies.
- If tempted to put `| None` after your type, check that this isn't a cop-out. Are you sure `None` should be allowed?
- Coerce untyped data (e.g., JSON API responses) to structured types as close to the boundary as possible — immediately inside the API call layer, not deep in business logic.
- All monetary values should be stored as `float`, never as strings with currency prefixes.

## Module Structure

### Single Responsibility

Each module should have one reason to change.

```
houses/
├── server.py          # HTTP endpoint, request handling
├── models.py          # Pydantic data models
├── config.py          # Configuration from env vars
├── enricher.py        # Transit + petrol + school enrichment
├── sheets.py          # Google Sheets write
├── retry.py           # Async retry with backoff
├── walkability.py     # Google Maps + ORS walking (planned)
├── town_desc.py       # LLM town descriptions (planned)
├── council_tax.py     # Council tax lookup (planned)
└── ...
```

If you find yourself adding a function to a module that doesn't match its stated purpose, create a new module.

### Prefer Flat Structure

Keep modules flat in the `houses/` directory rather than nesting them in subdirectories. Deep nesting hides information and makes imports harder to follow.

- `walkability.py` not `enrichment/walkability/walkability.py`
- `town_desc.py` not `enrichment/town/town_desc.py`

## Principles

### Fail Fast

- Decide what should happen and fail fast if it doesn't happen.
- Don't silence errors with empty `except` blocks.
- Don't provide default values where there is no good default (e.g., API keys should be configured, not defaulted to empty strings that silently skip enrichment).
- When an external API call fails, log the warning and return `None` — the caller decides the graceful degradation strategy.
- **A warning that still proceeds is wasted code.** If a condition is bad enough to warn about, it's bad enough to fail. The only exception is when the caller explicitly opted into the risk (e.g. `--obliterate`).
- **Don't waste API credits.** Backfill scripts must only enrich columns that are empty. Running full enrichment on existing data when only a few columns need updating is a bug. The server should refuse to do this unless explicitly told to.
- **Every API call should be necessary and justified.** If you're debugging, construct models directly instead of hitting real endpoints.

### Prefer Libraries Over Reinvention

Before writing non-trivial code from scratch, check whether a library already solves the problem. The decision criterion is simplicity and readability: a library call that replaces 30 lines of custom code is worth it; a library that adds more complexity than the code it replaces is not.

### No Over-Abstraction

This is a small, focused project. Do not create:
- Pipeline classes or orchestration frameworks
- Abstract base classes with a single concrete implementation
- Plugin systems or dynamic discovery
- Microservices or inter-process communication

Write straightforward functions that call each other. Use Pydantic for data, not for simulating a type system.

### DRY: Extract Shared Logic

When the same pattern appears in multiple places (e.g., API retry logic, geocoding, column index lookups), extract it into a shared function or module. The `retry.py` module is a good example.

### No `global` Keyword

Never use the `global` keyword. It makes a function's mutable state dependencies invisible — nothing at the call site tells you the function is modifying module state.

```
# GOOD
class _APIState:
    places_exhausted: bool = False
    ors_geo_exhausted: bool = False
_api_state = _APIState()

def enrich():
    if not _api_state.ors_geo_exhausted:
        ...
        _api_state.ors_geo_exhausted = True

# BAD — global keyword, invisible coupling
_ors_geo_exhausted = False

def enrich():
    global _ors_geo_exhausted
    if not _ors_geo_exhausted:
        ...
        _ors_geo_exhausted = True
```

Wrap module-level mutable state in a plain class instead. The class name describes what the state represents (`_APIState`, `_GeocodeRateLimit`, `_EnrichmentCache`), not the pattern name (`_RunState`, `_MutableState`). The instance is a module-private singleton (`_api_state`, `_geo_rate_limit`).

### Import Discipline

- Internal code within the package imports from sibling modules directly (`from houses.enricher import compute_transit`).
- `__init__.py` is kept minimal — just the package docstring.
- Don't import from submodule paths that don't exist yet for code that hasn't been written.

### Never Trash the Sheet

- **Never clear and regenerate the whole sheet.** Always use `scripts/update_sheet.py --columns "Col1,Col2"` to update only the columns that changed. Manual data (listing addresses, notes, status) is irreplaceable.
- If `update_sheet.py` doesn't support the columns you need, extend it — don't rewrite the backfill.
- The only exception is the very first sheet setup via `scripts/setup_sheet.py` which creates the tabs.
- A full clear + rewrite (`ws.clear()` followed by backfill) is forbidden. It wastes API calls, destroys manual data, and breaks the View tab formulas.

### Never Manipulate the Sheet Grid Directly

- **Do not call `insert_cols`, `deleteDimension`, `add_cols`, or `clear` on the sheet to restructure columns.** These operations are destructive and error-prone. The standard pipeline handles column structure changes safely.
- **To add a new column:** add it to `COLUMN_HEADERS` in `sheets.py` and include it in `_row_values()`. Then run `update_sheet.py` — it detects new columns automatically (old value is empty, new value has data) and writes only the changes.
- **To rename or reorder columns:** update `COLUMN_HEADERS` and `_row_values()`, then run `update_sheet.py`. The script compares old vs new row layouts cell-by-cell.
- **To move an existing column:** use the Sheets API `moveDimension` request — it's atomic, preserves data, and shifts surrounding columns automatically. Never use delete+insert or clear+rewrite.
- **If you must manipulate the sheet grid** (e.g., a one-time migration), use `moveDimension` rather than `insertDimension`, `deleteDimension`, or `clear`. Always verify by reading `get_all_values()` after the operation.

### User Columns Are Never Overwritten

- **User-provided columns** (Rightmove URL, Address, Postcode, Bedrooms, Price, Actual Latitude, Actual Longitude, Actual Postcode) must never be written by the server. `_row_values()` returns `""` for all of them.
- **The Rightmove ID column is the server's stable lookup key** — it sits between user columns and enriched columns and is the only non-user column on the left side of the sheet.
- **All columns after the Rightmove ID are enriched** — the server writes enrichment data there.
- `write_enriched_row` uses the Rightmove ID column to find existing rows. It only writes non-empty cells to avoid blanking user data.
- `update_sheet.py` MUST list all user column headers in `MANUAL_COLS` to prevent the backfill from ever touching them.
- **Never refer to columns by letter or index in documentation.** Columns shift when new ones are added. Always refer to them by their header name (e.g. "Address" not "C" or "B").

### No Mystery Code

- **Never use raw integers as column indices, array positions, or enum values.** Use named lookups: `col_index("Bedrooms")` not `3`, `col_letter(col_index("Simon London (min)"))` not `"I"`.
- **If a magic number or string has to exist**, wrap it behind a function or constant with a domain-meaningful name. A comment explaining what `3` means is still magic — put it behind `col_index("Bedrooms")` so the code reads naturally.
- **String literals that represent domain concepts** (sheet tab names, status values, API URLs) must be named constants. `sh.worksheet(DATA_TAB)` not `sh.worksheet("Properties Data")`.
- **Any non-trivial block of code that does something domain-specific should be extracted into a named function.** The function name serves as documentation. The body of `nearest_station()` is easier to understand than inline Haversine math with a comment.
- **If you're tempted to write a comment explaining what a block of code does, extract it into a function instead.** The function name replaces the comment. The body of the function can then be read or ignored as needed.
- **Docstrings on extracted functions are fine** — they document contracts and edge cases that aren't obvious from the code. But prefer code that doesn't need a docstring to be understood.
- **Exception**: zero, one, empty string, booleans, and trivial inline operations are fine (`if not results:`, `for i in range(count):`). Use judgment — the rule is "name it so the reader doesn't have to decode intent."

### API Keys and Secrets

- **Never read, log, print, echo, or store API keys** in conversation context, files, or code. Keys come from the environment only.
- The `.env` file is for non-secret configuration only. API keys live in the shell environment (`.zshrc`, `.bashrc`, `~/.profile`).
- Never add an API key to `.env` or any file that could be committed.
- Never print or log an API key value — even masked copies risk exposure.

## Smells

- **Long file**: A signal that multiple concerns have become mixed together. If `enricher.py` grows beyond 500 lines, split the unrelated enrichment types into their own modules.
- **Circular imports**: Fix the smell, don't bodge the import with lazy imports or inline imports.
- **Empty `except` blocks**: Never. Always catch specific exceptions and at minimum log them.
- **Type suppression**: Never use `# type: ignore` without a comment explaining why. Prefer fixing the type.

## Documentation and Deprecation

- **Delete, don't archive**. Obsolete files and content are a liability — they confuse readers and become stale. When something is no longer accurate, delete it. Don't rename it "legacy", don't move it to an archive directory, don't leave a deprecation notice that nobody reads. If it's wrong, remove it.
- **Single source of truth**: Each piece of information lives in exactly one place. Other docs link to it. They don't repeat it. If you find duplicated content, pick one home and link from the other locations.
- **Docs must match the code**: When you rename a function, module, or tab, update the docs in the same commit. When you add a feature, document it before moving on. Outdated docs are noise.
