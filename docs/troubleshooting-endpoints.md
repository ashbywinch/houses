# Troubleshooting Endpoints — What to Do

## Before Running a Batch Operation

1. **Wait for the server to stabilise.** After any code change, watch the
   server logs for `Application startup complete.` before curling an endpoint.
   If you see another `WatchFiles detected changes in...` right after, the
   server is still catching up from your latest edit — wait for it to finish.

2. **Don't edit files or commit while a batch is running.** Every file change
   triggers a `--reload` restart, which kills any in-progress HTTP request.
   The request will return a truncated response and the sheet won't be updated.

3. **Verify the commit hash in the startup log.** The server logs
   `Deploy: <short-hash>` on startup. If it doesn't match what you expect,
   the server hasn't loaded your latest changes. Touch the file you changed
   to force a reload: `touch houses/server.py && sleep 3`.

## While Inspecting Results

1. **Don't trust 200 OK from a streaming endpoint.** Uvicorn logs the status
   code when the response **starts** being sent. If the streaming generator
   is interrupted by a server restart mid-stream, the log still says 200 OK
   but the body is incomplete.

2. **Check for the summary line.** A completed batch always ends with:
   ```json
   {"type": "summary", "updated": 40, "skipped": 1, "created": 0, "errors": 0}
   ```
   If this line is absent, the request didn't finish. Try again without any
   edits in between.

3. **Check the server logs.** Successful writes produce:
   ```
   Wrote row 42 (RID 173638931): 6 cells [Simon London (min), ...]
   ```
   Skipped rows produce:
   ```
   Skipped row 25 (RID 174014342): 6 cells already had data [...]
   ```
   If neither appears, the write function crashed or the request was killed.

4. **Read `/properties/{rid}` to see what's actually in the sheet.** The
   compare endpoint shows what the enrichment *would* write, not what's
   already on the sheet. If they differ, you need to re-run with `force=true`.

## When Using `force=true`

The `force` query parameter must reach two places in the code:
- `_batch_stream()` — uses `force` to decide which columns to consider
- `_write_backfill_cells()` — uses `force` to decide whether to overwrite

Both must receive the parameter. If one is missed, the batch silently
skips every cell. Verify by searching the call chain for `force=`.

## Checking API Health

- **Google Maps Geocoding**: Check the server log for
  `"Geocoded '...' via google-maps"`. If you see `"ors-pelias"` instead,
  Google Maps is unavailable (403/REQUEST_DENIED).
- **Google Routes**: Check for `"google-routes: HTTP 4xx on attempt 1"`.
  If 403, the API key doesn't have this endpoint enabled or IP is blocked.
- **TfL**: Check for `"TfL transit failed"` or TfL disambiguation warnings.
- **ORS**: Check for `"ORS geocoding failed"`. The `_geo_state.ors_geo_exhausted`
  flag blocks further ORS calls after a 429/403.

## Use the Live Server Logs

The server logs are accessible via the background process tool. Check them
for warnings, errors, and Write/Skipped messages. If a batch seems stuck,
read the log — the error is probably there.

## Re-Running After Fixes

After fixing a bug in the batch logic:
1. Wait for the server to reload (watch for `Application startup complete.`).
2. Run the curl again.
3. Check both the streaming response tail AND the sheet via
   `/properties/{rid}` — the compare endpoint alone isn't enough.
