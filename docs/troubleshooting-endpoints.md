# Troubleshooting Endpoints

## Before a batch operation

Wait for the server to stabilise after code changes — watch logs for `Application startup complete.` before curling. A `WatchFiles detected changes in...` right after means it's still catching up; wait.

**Don't edit files or commit while a batch runs.** Every file change triggers `--reload`, killing in-progress HTTP requests → truncated response, sheet not updated.

## Verifying a batch completed

**Don't trust 200 OK from a streaming endpoint.** Uvicorn logs the status when the response *starts*; a mid-stream restart still logs 200 OK with an incomplete body.

A completed batch always ends with:

```json
{"type": "summary", "updated": 40, "skipped": 1, "created": 0, "errors": 0}
```

Absent → the request didn't finish. Check logs:

- Success: `Wrote row 42 (RID 173638931): 6 cells [...]`
- Skipped (no force, cells have data): `Skipped row 25 (RID 174014342): 6 cells already had data [...]`
- Neither → write function crashed or request was killed.

Skipped rows on `force=false` are expected — it only fills blanks. Overwrite with `force=true`.

## Checking API failures (server logs)

- **Google Maps Geocoding**: `"status=REQUEST_DENIED msg=..."` or `"Google Maps API response for '...': status=..."`. Success logs `"Geocoded '...' via google-maps"`.
- **Google Routes**: `"google-routes: HTTP 403 on attempt 1"` or `"Google Routes API error for ..."` — EndpointClient logs status and whether permanently blocked.
- **TfL**: `"TfL transit failed for ..."` or disambiguation warnings; error details in the API response body.
- **ORS**: `"ORS geocoding failed for ..."` — after repeated failures the ORS client is marked exhausted for the session.

## Reading compare output

`/properties/compare` shows a TSV diff: `RID  Field  Old (sheet)  New (enriched)`.

- `New` empty but a value expected → enrichment skipped (not requested, or field maps to no enrichment).
- `Old` wrong, `New` right → re-run batch with `force=true` to overwrite.

## The `force` parameter

- `force=false` (default): fill blank cells only. Incremental enrichment.
- `force=true`: overwrite all cells in requested fields. Only when new data is known better.

`force` must reach BOTH `_batch_stream()` and `_write_backfill_cells()`. If the call chain drops it, every cell is treated as "already has data" regardless of the query param — search call sites for `force=`.

## If the sheet wasn't updated

1. Check for `"type": "summary"` — absent = killed by server restart.
2. Check logs for `Wrote row` — absent = crash or killed request.
3. Re-run after the server stabilises (no file changes during run).
4. Comparing old vs new? Use `/properties/{rid}` to read what's actually on the sheet — compare shows what *would* be written, not what's there.

## Use the live server logs

| Log line | Meaning |
|---|---|
| `Wrote row` | data written to sheet |
| `Skipped row` | cells skipped (had data, no force) |
| `Batch done` | batch completed |
| `ERROR: Exception in ASGI application` | crash — check traceback for the underlying exception |
| API-specific errors | see "Checking API failures" |
