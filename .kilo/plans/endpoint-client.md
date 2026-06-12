# EndpointClient — Reusable Retry Logic for APIs

## Problem

Google Routes, TfL, and ORS all have different retry patterns scattered
across routing.py, transit_route.py, and location.py.  The current
_GoogleRoutesClient has no session-level backoff — when retries are
exhausted on one row, the next row immediately hits the same blocked
API again.

## Requirements

1. **Never try again within Retry-After period** — if the API says
   "retry after 60s", respect it even across different properties.
2. **If retries exhausted, don't try this API again during this endpoint
   call** — once we give up on Google Routes for one property in a
   `/properties` batch, subsequent properties skip that API.
3. **No Retry-After header** — fall back to exponential backoff.
   If retries are exhausted, still block for the call duration
   (hitting a 429 again will just get another 429).
4. **403 => never retry** (won't self-resolve).  Permanently blocked
   for the call duration.
5. **5xx => retryable** (transient server error).
6. **Clear logging** — each retry logs the delay, remaining budget,
   and whether it's from Retry-After or exponential backoff.

## EndpointClient Design

```python
class EndpointClient:
    """Reusable client for a single API endpoint.

    Tracks retry state across multiple calls within the same request
    lifecycle (e.g. a /properties batch).  Once retries are exhausted
    the API is skipped for the rest of the batch.
    """

    def __init__(self, name: str, max_retries: int = 3,
                 base_delay: float = 1.0, backoff: float = 2.0,
                 max_cumulative_delay: float = 180.0):
        self.name = name
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.backoff = backoff
        self.max_cumulative_delay = max_cumulative_delay  # 3 min budget
        self._blocked_until: float = 0.0
        self._permanently_blocked: bool = False  # 403, invalid key
        self._cumulative_blocked: bool = False   # retries exhausted

    @property
    def blocked(self) -> bool:
        return (self._permanently_blocked
                or self._cumulative_blocked
                or time.time() < self._blocked_until)

    def request(self, fn: Callable[[], Awaitable[dict]],
                fn_hash: str = "") -> Awaitable[dict | None]:
        """Execute *fn*, retrying on rate limits / connection errors.

        *fn* should raise ``HttpError`` on 429, ``httpx.HTTPStatusError``
        on other HTTP errors, or return the JSON response dict.
        """
```

## Tests (in order)

1. `test_403_permanently_blocks` — fn raises HttpError(403),
   assert first call tries, assert second call returns None (blocked)
2. `test_429_retried_then_blocks` — fn raises HttpError(429) with
   Retry-After: 0.01, assert retried, then assert subsequent calls
   are blocked
3. `test_429_without_retry_after_uses_backoff` — fn raises HttpError(429)
   without Retry-After header, assert retry delay uses exponential backoff
4. `test_exhausted_retries_block` — fn raises HttpError(429) repeatedly,
   assert after max_retries the client is blocked
5. `test_connection_error_retried` — fn raises httpx.ConnectError,
   assert retried with exponential backoff
6. `test_5xx_retried` — fn raises HttpError(500), assert retried
7. `test_404_not_retried` — fn raises httpx.HTTPStatusError(404),
   assert not retried (single attempt, returns None)
8. `test_cumulative_budget_exhausted` — multiple short retries exhaust
   the 180s budget → blocked
9. `test_retry_after_respected_across_calls` — first call gets 429 with
   10s Retry-After, second call <10s later returns None (blocked)

## Integration: Routing.py changes

1. Delete ``_GoogleRoutesClient`` class entirely.
2. Create module-level clients:
   ```python
   _google_routes = EndpointClient("google-routes", max_retries=2, base_delay=2.0)
   ```
3. Update ``_walk_commute`` → use ``_google_routes.request(_do_post)``
4. Update ``_google_transit_commute`` → use ``_google_routes.request(_do_post)``
5. Update ``_find_bus_alternative`` → use ``_google_routes.request(_do_post)``
6. Remove ``max_retries`` from ``retry_async`` call — EndpointClient owns it.
7. Remove ``_blocked`` flag from test_GoogleRoutesClient tests (already done).
