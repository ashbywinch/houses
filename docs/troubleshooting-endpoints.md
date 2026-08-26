# Troubleshooting Endpoints

## Before a long operation

Wait for the server to stabilise after code changes — watch logs for `Application startup complete.` before curling. A `WatchFiles detected changes in...` right after means it's still catching up; wait.

**Don't edit files or commit while a long request runs.** Every file change triggers `--reload`, killing in-progress HTTP requests → truncated responses and half-applied work.

## Checking API failures (server logs)

- **Google Maps Geocoding**: quota exhaustion logs `Skipping Google Maps — API quota exhausted`; success logs `Geocoded '...' via google-maps`. Fallbacks report `rate limit exhausted` once a provider is marked exhausted for the session (see the geo state in `houses/location.py`).
- **TfL**: repeated failures escalate to `TfL transient errors exhausted for <station> — ...`; error details also land in the API response body.
- **Any enrichment module**: failures surface as failed `Attempt`s — inspect them via the property JSON rather than guessing.

## Use the live server logs

| Log line | Meaning |
|---|---|
| `Application startup complete.` | server ready — safe to curl |
| `WatchFiles detected changes in...` | reload in progress — wait before the next request |
| `ERROR: Exception in ASGI application` | crash — read the traceback below it |
| API-specific errors | see "Checking API failures" |

## Health

`GET /health` returns `{"status": "ok"}` while the process is up.
