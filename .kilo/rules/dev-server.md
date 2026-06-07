# Dev Server Auto-Reload

The dev server (`make run`) starts uvicorn with ``--reload``, so it automatically picks up code changes without a restart.

**Never manually restart the dev server after editing code.** The reload happens within seconds. If the server appears stuck after a change, wait for the reload log message rather than killing and restarting.
