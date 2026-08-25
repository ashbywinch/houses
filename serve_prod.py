"""Serve the production build — mount built frontend and run."""

from pathlib import Path

import uvicorn
from fastapi.staticfiles import StaticFiles

from houses.settings import settings
from houses.server import app

build_dir = Path("houses/frontend/dist")
if build_dir.exists():
    app.mount("/", StaticFiles(directory=str(build_dir), html=True), name="frontend")

uvicorn.run(app, host=settings.host, port=settings.port, reload=False)
