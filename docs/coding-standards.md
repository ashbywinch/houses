# Coding Standards — Houses

Project-specific rules that supplement the shared coding standards.
Read both. If they conflict, this file takes precedence.

## Module Structure

```
houses/
├── server.py          # HTTP endpoint, request handling
├── models.py          # Pydantic data models
├── enricher.py        # Enrichment coordinators
├── sheets.py          # Google Sheets write
├── retry.py           # Async retry with backoff
├── routing.py         # Transit/drive routing dispatch
├── stations.py        # Station class + registry
├── bus_journey.py     # Bus fare zone data
├── commute.py         # Commute value objects
├── endpoint_client.py # API client with Retry-After
└── ...
```

Each module should have one reason to change.

### Each Class in Its Own Module

Each class should be in its own module, named after that class. The
exception is a module that groups closely related small dataclasses —
for example, `models.py` bundling several small models is fine because
each is just a handful of fields with no behaviour and they share the
same reason to change (the data schema). If a class grows non-trivial
behaviour, extract it to its own module.

## Houses-Specific Practices

### Never Trash the Sheet

- Never clear and regenerate the whole sheet. Manual data (listing
  addresses, notes, status) is irreplaceable.
- A full clear + rewrite (`ws.clear()` followed by backfill) is
  forbidden. It destroys manual data and breaks View tab formulas.
- Use `POST /properties?fields=...&force=true` to update specific
  columns that need refreshing.

### Never Manipulate the Sheet Grid Directly

- Do not call `insert_cols`, `deleteDimension`, `add_cols`, or `clear`
  to restructure columns. These operations are destructive and error-prone.
- To add a column: add it to `COLUMN_HEADERS` in `sheets.py` and include
  it in `_row_values()`. Run the batch refresh.
- To rename or reorder: update `COLUMN_HEADERS` and `_row_values()`, then
  run a full refresh.
- If you must manipulate the grid, use `moveDimension` — it's atomic,
  preserves data, and shifts surrounding columns automatically.

### User Columns Are Never Overwritten

- User-provided columns (Rightmove URL, Address, Postcode, Bedrooms,
  Price, Actual Latitude, Actual Longitude, Actual Postcode) must never
  be written by the server. `_row_values()` returns `""` for all of them.
- The Rightmove ID column is the server's stable lookup key.
- `write_enriched_row` uses the Rightmove ID column to find existing rows.
  It only writes non-empty cells to avoid blanking user data.

### API Keys and Secrets

- Keys come from the environment only. The `.env` file is for non-secret
  configuration.
- Never read, log, print, echo, or store API keys in conversation
  context, files, or code.

### Fail Fast, Don't Pre-Check

- Don't check for failure before trying an operation — just let the code
  fail naturally. The shared coding standards call this principle explicitly:
  "Don't silence errors with fallbacks BUT don't check for failure before
  trying, just let the code fail."
- A function should not pre-validate API keys before making the call.
  The HTTP transport mock handles requests in tests regardless of the key
  value. In production, a missing key causes a 403 which propagates as a
  regular API error.

### Cache Key Hygiene

- Never include API keys in cache key parameters. Credential rotation
  should not invalidate the cache.
- Do not cache non-OK API responses (e.g., `REQUEST_DENIED`). A temporary
  key issue should not poison the cache permanently.

### Force Parameter Discipline

- `force=true` overwrites existing cells. Use only when you know the new
  data is better than what is in the sheet.
- `force=false` (default) only fills blank cells. This is the safe default
  for incremental enrichment.
- The `force` parameter must reach BOTH `_batch_stream()` and
  `_write_backfill_cells()`. If the call chain drops it, every cell is
  treated as "already has data" regardless of the query parameter.

### Querying Properties

- `GET /properties` and `GET /properties/{rid}` require a `?tab=view` or
  `?tab=data` parameter. Without it, the endpoint returns an error.
- The View tab has XLOOKUP formulas that reference the Data tab. After
  writing data, call `POST /sync-view-formulas` if needed.

## Testing

### Mock External APIs in Every Integration Test

Integration tests must never hit real APIs. The conftest automates this with
an autouse fixture that patches ``httpx.AsyncClient`` and ``httpx.Client``
with a ``MockTransport``. Every test automatically gets mocked HTTP responses.

If you need a different response for a specific test, add a custom rule to
the handler via ``handler.add_rule(matcher, responder)``.

Do not rely on real API availability or fixture cache files for integration
test correctness. The mock transport is the source of truth.

### Fixture API Cache

Integration tests that exercise HTTP-transport code should never need
live API credentials. The `api_cache` module provides disk-backed caching.
`tests/integration/conftest.py` isolates each test to a temporary copy of
`tests/fixtures/api_cache/` — pre-seeded fixture cache files. During
development, run the test once with real API keys to populate the cache;
commit the resulting `.json` files as permanent fixtures.

When bootstrapping new fixtures:
```bash
uv run pytest tests/integration/test_sheet_update.py -k test_dry_run
cp data/api_cache/*.json tests/fixtures/api_cache/
git add tests/fixtures/api_cache/
git commit -m "Add api_cache fixtures for new integration test"
```

### Test Organization

- **Unit tests** (`tests/unit/`): Test one function or module in isolation.
  Mock only the boundary (HTTP, I/O). No real API calls.
- **Integration tests** (`tests/integration/`): Test the full pipeline with
  synthetic (mocked) HTTP responses.
- **E2E tests** (marked `@pytest.mark.e2e`): Verify that real external APIs
  behave as expected. **One consolidated suite per external API.**

Marker convention:
- `@pytest.mark.integration` — full pipeline with mocked transport
- `@pytest.mark.e2e` — real API calls (skipped by default)

### E2E Discipline

- Consolidate all e2e tests into a single location rather than scattering
  them across multiple files.
- Each e2e test must test exactly one API contract. Do not combine multiple
  API assertions in one test.
- E2E tests must never silently pass. Either assert the result or
  `pytest.skip()` with the reason.
- All e2e tests are always skipped by default
  (`addopts = ["-m", "not e2e"]` in `pyproject.toml`).

### Validate Test Data Against Production Constants

When a test class defines data that mirrors a production constant (column
headers, config values, enum-like sets), include a test that asserts the
test data matches the canonical source:

```python
def test_column_headers_match_sheets(self):
    assert self.DATA_HEADERS == COLUMN_HEADERS
```

This prevents silent drift: when the production constant changes, the test
fails and reminds the developer to update the test data.

### Test Invariants

In addition to per-function tests, include invariant tests that catch
regressions in cross-cutting concerns:

- "Every column in COLUMN_HEADERS has a corresponding field in
  `_row_values()`"
- "All View headers have either a formula or are marked manual"
- "`_build_full_row()` output has same length as `COLUMN_HEADERS`"
- "No View formula uses hardcoded cross-sheet references or IFERROR"

These tests enforce structural contracts that individual function tests
might miss.

## Documentation

- **Delete, don't archive.** Obsolete content is a liability. When something
  is no longer accurate, delete it. Don't move it to an archive, don't leave
  a deprecation notice. If it's wrong, remove it.
- **Single source of truth**: Each piece of information lives in exactly one
  place. Other docs link to it. They don't repeat it. If you find duplicated
  content, pick one home and link from the other locations.
- **Docs must match the code**: When you rename a function, module, or tab,
  update the docs in the same commit.
