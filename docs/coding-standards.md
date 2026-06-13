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

### Column Migrations

- Use `scripts/sheet_tool.py` for column operations: `add`, `move`, `rename`,
  `delete`. This is the only tool for grid manipulation. Do not call
  `insert_cols`, `deleteDimension`, `add_cols`, or `clear` directly.
- After a column change, call `POST /sync-view-formulas` to refresh View tab
  formulas and named ranges to match the new column positions.
- Delete one-off migration scripts after they've been run. The git log
  preserves the history.
- Update `COLUMN_HEADERS` and `_row_values()` in `sheets.py` to match the
  new column layout. Run a batch refresh to populate the new column.

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

## Dependency Injection

The shared coding standards describe three DI patterns: local `_kwarg`
injection, `Services` composition root, and context vars. This project
uses all three — see how each is applied here:

| Pattern | Houses implementation | When to use |
|---------|----------------------|-------------|
| **`Services` container** | `houses/services.py` — `Services` dataclass with every enrichment service and real defaults. `_run_enrichment` accepts optional `services` param. | Replace an entire enrichment module (EPC, council tax, commute) |
| **Context vars** | `houses/context.py` — `get_services()`, `get_bus_fare_reader()`, `get_sheets_client()`. Server middleware initialises per-request state. | Per-request singletons (bus fares, sheets client, geo state) |
| **Local `_kwarg`** | `_registry` on `_add_parking_cost`, `_page_path` on `scrape()`, etc. | Leaf-level data objects (car park data, HTML fixtures) |

Reusable fakes live in `tests/helpers.py`. Use `make_services()` to build a
`Services` with all fakes at sensible defaults, or construct a custom
`Fake*` for individual service overrides.

## Testing

### Three Mocking Layers

Tests run at three boundaries, from simplest to most thorough:

**1. Pure functions** — no mocking at all. Test real logic with real inputs
and assert output values. (Most of ``tests/unit/`` works this way.)

**2. Function-parameter injection** — pass a fake service or data object
via the ``_kwarg`` pattern. No monkeypatch, no MockTransport.

```python
result = await route._add_parking_cost(data, 30.0, _registry=registry)
```

**3. ``Services`` container** — build a ``Services`` with fakes and pass
to ``_run_enrichment``.

```python
from tests.helpers import make_services

services = make_services(
    epc_service=FakeEPC(band="C"),
    commute_router=FakeCommuteRouter(simon=None),
)
result = await _run_enrichment(..., services=services)
```

**4. ``ContextVar``** — set per-request state for the test scope.

```python
import houses.context as ctx

token = ctx._request_bus_fares.set(my_registry)
try:
    result = await get_commute(...)
finally:
    ctx._request_bus_fares.reset(token)
```

**5. MockTransport** (legacy) — the integration conftest patches httpx at
the transport layer.  Works for tests that need fine-grained HTTP response
control. Defined in ``tests/integration/conftest.py``.

### Reusable Fakes

``tests/helpers.py`` provides ready-made fakes for every service:

| Fake | Overrides |
|------|-----------|
| ``FakeGeocoder`` | ``result``, ``postcode_override`` |
| ``FakeCommuteRouter`` | ``simon``, ``lorena``, ``petrol`` |
| ``FakeEPC`` | ``band`` |
| ``FakeCouncilTax`` | ``band``, ``cost`` |
| ``FakeWalkability`` | ``walk_to_town_minutes``, ``amenities`` |
| ``FakeTownDesc`` | ``description`` |
| ``FakeSchoolLookup`` | returns ``None`` for all lookups |
| ``FakeRailFare`` | passes simon/lorena through unchanged |

Use ``make_services()`` for a ``Services`` with all fakes at sensible
defaults:

```python
services = make_services(epc_service=FakeEPC(band="B"))
```

### Test Organization

- **Unit tests** (`tests/unit/`): Test one function or module in isolation.
  No real API calls. Prefer ``_kwarg`` injection or pure-function tests.
- **Integration tests** (`tests/integration/`): Test the full pipeline.
  Can use ``Services`` fakes, ``ContextVar``, or MockTransport.
- **E2E tests** (marked ``@pytest.mark.e2e``): Verify real external APIs.
  **One consolidated suite per external API.** Skipped by default.

### MockTransport (Legacy — For Migration Only)

The integration conftest patches ``httpx.AsyncClient`` and ``httpx.Client``
with a ``MockTransport``. New tests should prefer ``Services`` or
``ContextVar`` DI instead. When converting a MockTransport test to DI:

1. Identify which enrichment services the test exercises.
2. Create fakes via ``tests/helpers.py``.
3. Pass ``services=make_services(...)`` to ``_run_enrichment``.
4. Remove the test from ``_mock_http_requests`` dependency.

## Documentation

- **Delete, don't archive.** Obsolete content is a liability. When something
  is no longer accurate, delete it. Don't move it to an archive, don't leave
  a deprecation notice. If it's wrong, remove it.
- **Single source of truth**: Each piece of information lives in exactly one
  place. Other docs link to it. They don't repeat it. If you find duplicated
  content, pick one home and link from the other locations.
- **Docs must match the code**: When you rename a function, module, or tab,
  update the docs in the same commit.
