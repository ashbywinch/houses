# Troubleshooting Endpoints

## Before Running a Batch Operation

Wait for the server to stabilise after code changes. Watch the server logs
for `Application startup complete.` before curling an endpoint. If you see
another `WatchFiles detected changes in...` immediately after, the server is
still catching up — wait for it to finish.

Don't edit files or commit while a batch is running. Every file change
triggers a `--reload` restart that kills any in-progress HTTP request. The
response will be truncated and the sheet won't be updated.

## Verifying a Batch Completed

Don't trust 200 OK from a streaming endpoint. Uvicorn logs the status code
when the response starts being sent. If the generator is interrupted by a
server restart mid-stream, the log still says 200 OK but the body is
incomplete.

A completed batch always ends with:
```json
{"type": "summary", "updated": 40, "skipped": 1, "created": 0, "errors": 0}
```
If this line is absent, the request didn't finish.

Check the server logs. Successful writes produce:
```
Wrote row 42 (RID 173638931): 6 cells [...]
```
Skipped rows (no force, cells already have data):
```
Skipped row 25 (RID 174014342): 6 cells already had data [...]
```
If neither appears, the write function crashed or the request was killed.

If you're running a `force=false` batch and rows are being skipped, that's
the expected behaviour — `force=false` only fills blank cells. If you need
to overwrite, use `force=true`.

## Checking API Failures

Look for the API's own error messages in the server logs:

- **Google Maps Geocoding**: `"status=REQUEST_DENIED msg=..."` or
  `"Google Maps API response for '...': status=..."`. If the API returns
  results, it logs `"Geocoded '...' via google-maps"`.
- **Google Routes**: `"google-routes: HTTP 403 on attempt 1"` or
  `"Google Routes API error for ..."`. The EndpointClient logs the status
  code and whether it's permanently blocked.
- **TfL**: `"TfL transit failed for ..."` or TfL disambiguation warnings.
  The TfL API itself returns error details in the response body.
- **ORS**: `"ORS geocoding failed for ..."`. After repeated failures the
  ORS client is marked exhausted for the session.

## Reading the Compare Output

The `/properties/compare` endpoint shows a TSV diff between the existing
sheet values and a fresh enrichment. Each row has the format:

```
RID    Field    Old (sheet)    New (enriched)
```

If the `New` column is empty but you expected a value, the enrichment was
skipped (not requested, or the field doesn't map to any enrichment).
If the `Old` value looks wrong and the `New` value looks correct, you
need to re-run the batch with `force=true` to overwrite it.

## The `force` Parameter

`force=false` (default): only fill blank cells. Cells that already have
data are left untouched. Use this for incremental enrichment.

`force=true`: overwrite all cells in the requested fields, even if they
already have values. Only use this when you know the new data is better
than what's in the sheet.

The `force` parameter must reach both `_batch_stream()` and
`_write_backfill_cells()`. If the call chain drops it, every cell is
treated as "already has data" and skipped regardless of the query param.
Search the call sites for `force=` to verify.

## If the Sheet Wasn't Updated

1. Check the streaming response for the `"type": "summary"` line. If absent,
   the request was killed by a server restart.
2. Check the server logs for `Wrote row` messages. If absent, the write
   function crashed or the request was killed.
3. Re-run after the server stabilises (no file changes during the run).
4. If comparing old vs new, use `/properties/{rid}` to read what's actually
   on the sheet — the compare endpoint shows what *would* be written, not
   what's already there.

## Use the Live Server Logs

Read the background process logs to see what's happening during a batch.
Look for:
- `Wrote row` — confirms data was written to the sheet
- `Skipped row` — cells were skipped (already had data, no force)
- `Batch done` — confirms the batch completed
- `ERROR: Exception in ASGI application` — something crashed (check for
  the underlying exception in the traceback)
- API-specific error messages (see "Checking API Failures" above)
