# Coding Standards

These standards apply to all code in the houses project. They help readers (human or agent) understand what the code does and why it is structured that way.

## Naming

- **Name things after what they are in the domain**, not after their structural role in the architecture. A function that computes a transit commute should be named `compute_transit`, not `TransitOrchestrator` or `CommuteHandler`.
- **Classes represent things; functions represent actions.** A class name should be a noun from the problem space (`TransitInfo`, `SchoolInfo`, `PetrolCost`). A function name should be a verb (`compute_transit`, `find_nearest_boys_primary`, `write_enriched_row`).
- **Avoid vague suffixes** like "Manager", "Orchestrator", "Handler", "Controller", "Context", "Tools" in class and module names. If the domain concept is clear, the name will be simple.
- **If a class or function name uses vague terms** like "Enhanced" or "Configured", reconsider whether the base concept is well-defined. When two concepts genuinely need disambiguation, the names should complement each other (e.g. `EnrichedProperty` vs `RawProperty` — each clarifies the other).
- **The docstring test**: If the best docstring you can write merely rephrases the name (`TransitOrchestrator` → "orchestrates transit"), that is a naming smell. Either the name is too vague or the concept boundaries are unclear. Fix the name or split the concept — don't add a docstring that says nothing.
- **Module names should be domain-driven**, not structural. Use `walkability.py` not `walkability_utils.py`, `town_desc.py` not `town_description_tools.py`.
- A module named "utils" is a grab bag. It has no single responsibility. Name modules after what they do.

## Types

- Use expressive types. Pydantic models are the source of truth for data shapes. Multiple levels of nested `dict` or `Any` are not expressive — if you need structured data, define a model.
- If tempted to use `Any`, double-check whether a narrower type applies.
- If tempted to provide several types in a union (`str | int | float`), prefer standardising on a single type where possible. A wide union often means the conceptual boundary is fuzzy.
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

### Each Class in Its Own Module

Each class should be in its own module, named after that class (`Walkability` in `walkability.py`, `EnrichedProperty` in `enriched_property.py`). The exception is a module that groups closely related small dataclasses — for example, `models.py` bundling `TransitInfo`, `SchoolInfo`, and `PetrolCost` is fine because each is just a handful of fields with no behavior and they share the same reason to change (the data schema). If a class grows non-trivial behavior, extract it to its own module.

### Data Clumps → Classes

When the same two or more values always appear together — as function parameters, as tuple unpackings, or as dict keys — promote them to a class. `(lat, lng)` passed as separate parameters to six functions is a data clump. `{"walk_to_town_minutes": ..., "amenities": ...}"` constructed in five places with the same keys is a data clump. A repeating 7-parameter function signature is a data clump.

Name the class after the domain concept the clump represents, not after its shape (`GeoPoint` not `LatLngPair`, `CommuteRoute` not `CommuteConfig`). The class then becomes the home for methods that operate on that data — the haversine formula belongs on `GeoPoint`, the enrichment pipeline config belongs on `CommuteRoute`.

### Member Functions for Tightly Coupled Logic

Functionality that is tightly coupled to the contents of a class should be a member function of that class — serialization, computed properties, and validation are good candidates. Orchestration logic that coordinates across multiple data types still belongs in dedicated modules (e.g., `enricher.py` orchestrates transit + schools + petrol into an `EnrichedProperty`). The distinction is whether the logic changes when that class's structure changes, or when the pipeline between classes changes.

## Principles

### Fail Fast

- Decide what should happen and fail fast if it doesn't happen.
- Don't silence errors with empty `except` blocks.
- Don't provide default values where there is no good default (e.g., API keys should be configured, not defaulted to empty strings that silently skip enrichment).
- When an external API call fails, log the warning and return `None` — the caller decides the graceful degradation strategy.
- **A warning that still proceeds is wasted code.** If a condition is bad enough to warn about, it's bad enough to fail. The only exception is when the caller explicitly opted into the risk (e.g. `--obliterate`).
- **Don't waste API credits.** Backfill scripts must only enrich columns that are empty. Running full enrichment on existing data when only a few columns need updating is a bug. The server should refuse to do this unless explicitly told to.
- **Every API call should be necessary and justified.** If you're debugging, construct models directly instead of hitting real endpoints.

### Functional Programming Style

Write code in a functional style where reasonably possible: prefer pure functions that take inputs and return outputs, avoid hidden mutable state, and keep side effects at the edges of the system. This does not mean banishing all mutation — the `_APIState` pattern for rate limiting is a practical concession, and I/O (HTTP calls, sheet writes) is inherently stateful. The principle is: model your computation as data-in → data-out transformations, not as sequences of mutations.

**Never mutate function arguments.** If a function receives a dict or list and needs to modify it, create and return a new one instead. In-place mutation of arguments makes call-site behavior unpredictable — the caller cannot tell whether their data was modified without reading the callee's implementation.

### No Backwards Compatibility

This project has no external consumers or published releases. Do not preserve deprecated interfaces, maintain compatibility shims, or keep old code paths working for the sake of backwards compatibility. Delete the old thing, update the callers, move on. There is no support burden.

### Configuration Belongs in JSON, Not Code

Business logic configuration — petrol price, MPG, search radius, working weeks, commute frequency, LLM settings, and any other tunable parameter — must live in a JSON config file, not scattered across modules or hardcoded in `config.py`. A single `config.json` (or `~/.houses/config.json`) is the source of truth for every tunable value.

This means:
- `enricher.py` should never contain `mpg = 45` or `price_per_litre = 1.45`. Those come from the config file.
- `server.py` should never contain `tube_single = 2.80`. That comes from the config file.
- `config.py` loads from JSON + environment variables, it does not define defaults inline (except for truly fixed constants like API endpoint URLs).

API keys and other secrets remain in environment variables, not in the JSON config file. The JSON config is for non-secret parameters that affect behavior.

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

Duplication is a bug, not a shortcut. When the same pattern appears in multiple places — especially across module boundaries — extract it into a shared function or module. The `retry.py` module is a good example.

Cross-module duplication is particularly dangerous: independent copies of the same formula (haversine), regex (`_OUTCODE_RE`), API object (geocoding), or state tracker (`_APIState`) will inevitably drift. One copy gets fixed or improved, the others don't. If you find yourself copying code between modules, stop and extract it.

A useful heuristic: if you see the same function name in two modules (e.g., `_haversine_km` in `enricher.py`, `rail_fares.py`, and `walkability.py`), that's a signal the shared logic hasn't been extracted yet.

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

### Module Encapsulation

**Never import private symbols from another module.** The underscore prefix (`_geocode`, `_rightmove_id`, `_KNOWN_COUNTIES`) means "internal implementation detail, not part of the public API." Importing private symbols from another module creates hidden coupling: the exporting module considers them free to change, but importing modules depend on them.

If logic needs to be shared across modules, either:
- Make it public (remove the underscore) and document it as shared API — only do this if the function genuinely serves both modules' purposes
- Extract it to a shared module (e.g., a shared `geo.py` for geocoding, `math_utils.py` for haversine) that both modules import from

**Never import inside a function body.** Every import must be at the top of the file, visible at a glance. Inline imports hide dependencies from static analysis and make it impossible to tell what a module actually depends on without reading every function. If you have a circular import, fix the module structure — don't hide it behind a lazy import.

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

## Testing

### Fixture API Cache for Integration Tests

Integration tests that exercise HTTP-transport code (TfL, Google Maps, geocoding)
should never need live API credentials.  The ``api_cache`` module provides
disk-backed caching of all external API responses.  The ``tests/integration/conftest.py``
isolates each test to a temporary copy of ``tests/fixtures/api_cache/`` — pre-seeded
fixture cache files.  During development, run the test once with real API keys to
populate the cache; commit the resulting ``.json`` files as permanent fixtures.

When bootstrapping new fixtures:

```bash
# 1. Run the test with real API keys (generates cache files)
uv run pytest tests/integration/test_sheet_update.py -k test_dry_run
# 2. Copy the generated cache files to the fixture directory
cp data/api_cache/*.json tests/fixtures/api_cache/
# 3. Commit the fixtures
git add tests/fixtures/api_cache/
git commit -m "Add api_cache fixtures for new integration test"
```

Fixtures that test caching behaviour (call counts, cache misses) should break
out of the shared cache by monkeypatching ``houses.api_cache.CACHE_DIR`` to an
empty temporary directory before invoking the function under test.

### Test Behavior, Not Implementation

A test should verify that the system produces the correct output for a given input — not that specific internal functions were called with specific arguments. Asserts like `mock_function.assert_called_once_with(args)` test call patterns, not behavior. They break when the implementation is refactored even though the output is identical.

Good test asserts:
- Return values: `assert result == expected`
- Data structure: `assert result.field == value`
- Side effects at the boundary: `assert ws.update.call_args[0][0] == expected_cells` (sheet writes are a boundary)

Avoid:
- `assert mock_enricher.assert_called_once_with(postcode)` — the caller shouldn't care who was called
- `assert mock_write.call_args[0][6] == columns` — positional-argument indexing is brittle and tests implementation wiring

### Mock Only the Boundary

Mock at the system boundary: HTTP transport, sheet I/O, filesystem. Never mock the function-under-test's own dependencies at the Python level.

```
# GOOD — mock the HTTP transport, test the enrichment logic
original_init = AsyncClient.__init__
def _patched_init(self, **kwargs):
    kwargs["transport"] = MockTransport(handler)
    original_init(self, **kwargs)
with patch.object(AsyncClient, "__init__", _patched_init):
    result = await compute_transit(...)
assert result.min_time == expected

# BAD — mock the enrichment function, test nothing real
with patch("houses.enricher.compute_transit") as mock_fn:
    result = await inject_property(payload)
assert mock_fn.assert_called_once_with(postcode)
```

The transport-layer pattern verifies that your parsing, error handling, and data transformation all run for real. The Python-level pattern verifies only that a mock was called — it doesn't test whether the enrichment logic actually works.

**Exception**: If a dependency is genuinely at the boundary but has no mockable transport (e.g., `VOAClient` from `uk-property-apis`), mock the boundary-adjacent interface, not the function under test. Prefer mocking the HTTP response over mocking the wrapper class method.

### Shared Mock Infrastructure

The `AsyncClient.__init__` + `MockTransport` patching pattern is currently duplicated across 4 test files with near-identical code. Extract it into a shared helper in `conftest.py`:

```python
# In conftest.py:
@pytest.fixture
def mock_httpx(handler):
    """Context manager that patches httpx.AsyncClient with a MockTransport."""
    ...
```

A test file should not need 12 lines of boilerplate to mock HTTP for every test. If you see the same patching code in a second file, extract it.

### No `settings` Mutation in Tests

The pattern `original = settings.foo; settings.foo = "x"; try: ... finally: settings.foo = original` is error-prone and repetitive. Use a fixture:

```python
@pytest.fixture
def temp_sheet_id():
    original = settings.sheet_id
    settings.sheet_id = "test-sheet"
    yield
    settings.sheet_id = original
```

Or use `monkeypatch.setattr(settings, "sheet_id", "test-sheet")` for simpler cases. Either way, the cleanup must be automatic, not manual.

### Test Organization

- **Unit tests** (`tests/unit/`): Test one function or module in isolation. Mock only the boundary (HTTP, I/O). No real API calls.
- **Integration tests** (`tests/integration/`): Test the full enrichment pipeline with synthetic (mocked) HTTP responses. Validate that enrichment fields flow correctly through the server without hitting real APIs.
- **E2E tests** (`tests/integration/` marked `@pytest.mark.e2e`): Verify that real external APIs behave as the code expects. **One consolidated suite per external API**, not scattered across multiple files.

Marker convention:
- `@pytest.mark.integration` — full pipeline with mocked transport (run by default)
- `@pytest.mark.e2e` — real API calls (skipped by default, enabled via `make test-all`)

### E2E Discipline

E2e tests exist only to verify that external APIs haven't changed their contract. They are not for testing the application logic.

- Consolidate all e2e tests into a single location (e.g., `tests/integration/test_external_apis.py`) rather than scattering them across multiple files.
- Each e2e test must test exactly one API contract. Do not combine multiple API assertions in one test — if the postcodes.io test fails, the VOA test result is still useful.
- E2e tests must never silently pass. A test that returns early on rate-limiting (`if coords is None: return`) is worse than no test — it gives false confidence. Either assert the result or `pytest.skip()` with the reason.
- All e2e tests are always skipped by default (`addopts = ["-m", "not e2e"]` in `pyproject.toml`).

### No Sleep-Based Timing

`time.sleep(3)` in tests is a heuristic that fails on slow CI runners and wastes time on fast ones. Instead:
- For formula calculation waits: poll with a timeout (`try: ... for _ in range(30): ... else: raise`)
- For async delays: use `asyncio.wait_for()` or `anyio` timeouts
- For deterministic timing tests: mock the clock with `pytest-freezer` or `monkeypatch`

### Validate Test Data Against Production Constants

When a test class defines data that mirrors a production constant (column headers, config values, enum-like sets), include a test that asserts the test data matches the canonical source:

```python
def test_column_headers_match_sheets(self):
    assert self.DATA_HEADERS == COLUMN_HEADERS
```

This prevents silent drift: when the production constant changes, the test fails and reminds the developer to update the test data.

### Test Invariants

In addition to per-function tests, include invariant tests that catch regressions in cross-cutting concerns:

- "Every column in COLUMN_HEADERS has a corresponding field in `_row_values()`"
- "All View headers have either a formula or are marked manual"
- "`_build_full_row()` output has same length as `COLUMN_HEADERS`"
- "No View formula uses hardcoded cross-sheet references or IFERROR"

These tests enforce structural contracts that individual function tests might miss.

## Smells

- **Long file**: A signal that multiple concerns have become mixed together. If a module grows beyond 500 lines, identify the separate concerns and split them into their own modules. Each new module should have one reason to change.
- **Unbounded caches**: In-memory dicts that grow without limit (`_geo_cache`, `_town_cache`) leak memory and make tests order-dependent. Use bounded caches (`functools.lru_cache`, `cachetools.TTLCache`) or set an explicit maximum size.
- **Blocking the event loop**: Calling synchronous I/O (sync `httpx.Client`, `open()`, `time.sleep()`) inside async functions blocks the event loop for all concurrent operations. Use async clients (`httpx.AsyncClient`) and async filesystem APIs consistently. If you must call sync code, offload it to a thread with `asyncio.to_thread()`.
- **Dead code**: Unused models, unreachable branches, import-time-only scripts, and compatibility wrappers that no caller uses. Delete them. The `git log` has the old version if you ever need it back.
- **Circular imports**: Fix the smell, don't bodge the import with lazy imports or inline imports.
- **Circular docstring**: A docstring that adds no information beyond the name is a smell. The class may need a better name, clearer boundaries, or both. (Sometimes the class is genuinely self-describing with no need for a docstring — that is fine.)
- **Empty `except` blocks**: Never. Always catch specific exceptions and at minimum log them.
- **Type suppression**: Never use `# type: ignore` without a comment explaining why. Prefer fixing the type. If you're tempted to suppress a type error, consider whether it reveals a code or architecture smell that should be fixed instead.

## Documentation and Deprecation

- **Delete, don't archive**. Obsolete files and content are a liability — they confuse readers and become stale. When something is no longer accurate, delete it. Don't rename it "legacy", don't move it to an archive directory, don't leave a deprecation notice that nobody reads. If it's wrong, remove it.
- **Single source of truth**: Each piece of information lives in exactly one place. Other docs link to it. They don't repeat it. If you find duplicated content, pick one home and link from the other locations.
- **Single source of truth also applies to data**: Column-to-field mappings, formula logic, regex patterns, API endpoint URLs — any structured data that exists in multiple places will drift. If two modules define the same mapping (e.g., enrichment fields in `server.py` and `scripts/update_sheet.py`), consolidate them. A single import is better than two independent copies.
- **Docs must match the code**: When you rename a function, module, or tab, update the docs in the same commit. When you add a feature, document it before moving on. Outdated docs are noise.
