# Test-First Development

Always write tests before implementing new functionality. For every new endpoint, module, or behavior change:

1. Write the test(s) that define the expected behavior first.
2. Run the test(s) to confirm they fail (red).
3. Implement the minimal code to pass the test(s) (green).
4. Refactor as needed while keeping tests green.

Do not write implementation code before tests exist and have been run (and failed). This applies to all changes: new endpoints, new enrichment modules, sheet operations, scripts, and behavior modifications.

## Test Real Logic, Not Mock Call Patterns

Tests must exercise real functionality — not just assert that mocks were called with the right arguments. Mock only the boundary (HTTP APIs, sheet I/O, filesystem), then test the actual behavior:

- Call the real function and check its output for correctness.
- For functions that call an external API, mock the transport layer (e.g. `httpx` transport) so the response is synthetic but the parsing, error handling, and data transformation all run for real.
- Avoid mocking the function-under-test's dependencies at the Python level (e.g. `patch("houses.enricher._geocode")`) — instead mock the HTTP responses that function would receive.
- A test that asserts `mock_function.assert_called_once_with(args)` without checking the final output is testing implementation details, not behavior.

Examples:

| Good (tests behavior) | Avoid (tests mocks) |
|---|---|
| Mock httpx transport, call enrichment function, assert TransitInfo fields | Mock transit function, assert it was called with postcode |
| Create real `EnrichedProperty`, call `_row_values`, assert returned dict | Mock `_row_values`, assert it was called |

## Bug Fixes Require a Failing Test First

When fixing a bug, write a test that reproduces the bug before writing the fix. Run the test to confirm it fails (red), then implement the fix and verify the test passes (green). This applies to both pre-existing bugs and regressions introduced during development.

If the bug was discovered because an existing test is already failing, that test serves as the failing test — fix the implementation to make it pass again (or update the test if the expected behavior legitimately changed).

## Validate Test Data Against Canonical Sources

When a test contains data that mirrors a production constant (column header lists, config enums, etc.), add an explicit assertion that the test data matches the canonical source. For example, if a test class defines `DATA_HEADERS` for mocking a sheet, include a test like:

```python
def test_column_headers_match_sheets(self):
    assert self.DATA_HEADERS == COLUMN_HEADERS
```

This ensures that when the production constant changes, the test fails and reminds the developer to update the test data. Without this assertion, tests can silently diverge from reality, leading to false passes.
