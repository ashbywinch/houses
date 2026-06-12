# Dev Server Auto-Reload

The dev server (`make run`) starts uvicorn with ``--reload``, so it automatically picks up code changes without a restart.

**Never manually restart the dev server after editing code.** The reload happens within seconds. If the server appears stuck after a change, wait for the reload log message rather than killing and restarting.

**WatchFiles restarts kill in-progress HTTP requests.** Every file change triggers a full server restart, which terminates any streaming response (batch refresh, compare, etc.). The response will be truncated and the sheet won't be updated. Before running a long operation:
1. Let the server stabilise — watch for ``Application startup complete.``
2. Don't edit files or commit while a batch is running.
3. Check the streaming response for ``"type": "summary"`` to confirm completion.
4. Read ``docs/troubleshooting-endpoints.md`` for more detail.
