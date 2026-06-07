# Stream Command Output for Real-Time Visibility

Long-running operations must show progress as it happens so the user can see results in real-time instead of waiting for a final summary.

- Use streaming endpoints (`StreamingResponse` with newline-delimited JSON) for HTTP operations that process multiple items. Each item yields a JSON line as soon as it completes.
- Use `background_process` for long-running server commands so output appears in the sidebar.
- Do not buffer all results and return them at once. The user should see each row or task complete without waiting for the entire batch.
- Log progress server-side too, but stream to the HTTP client as the primary visibility mechanism.
- When using `curl` against a streaming endpoint, the `curl` output shows each line as it arrives — no special flags needed.
