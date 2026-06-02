"""Run the houses server via `python -m houses`."""

import uvicorn

from houses.config import settings


def main() -> None:
    uvicorn.run(
        "houses.server:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
    )


if __name__ == "__main__":
    main()
