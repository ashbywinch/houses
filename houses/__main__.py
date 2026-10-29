"""Run the houses server via `python -m houses`."""

import uvicorn

from houses.settings import settings


def main() -> None:
    uvicorn.run(
        "houses.server:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        # The app's packages only (see settings.reload_packages): watching the
        # whole tree meant a saved test restarted the backend — a full DAG
        # re-seed plus a code-stale recompute sweep — with no app change. An
        # emptied setting passes None, uvicorn's documented "no narrowing"
        # (it falls back to watching the working directory).
        reload_dirs=(list(settings.reload_packages) or None) if settings.reload else None,
    )


if __name__ == "__main__":
    main()
