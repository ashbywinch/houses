"""The dev server's reload scope.

uvicorn's reloader used to watch the whole working tree, so saving a test
restarted the backend — a full DAG re-seed plus a code-stale recompute sweep
— with no application change. The scope is now the packages the app imports;
these tests keep it complete (a new top-level package must not be silently
unwatched) and narrow (tests and docs must not restart the server).
"""

from __future__ import annotations

import ast
from pathlib import Path

import uvicorn

from houses.settings import settings


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _local_packages() -> set[str]:
    """The repo's own top-level packages (a directory with __init__.py)."""
    return {p.name for p in _repo_root().iterdir() if p.is_dir() and (p / "__init__.py").is_file()}


def _imported_roots() -> set[str]:
    """Every top-level module the app's own code imports (houses/, dag/)."""
    roots: set[str] = set()
    for package in ("houses", "dag"):
        for path in (_repo_root() / package).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    roots.add(node.module.split(".")[0])
                elif isinstance(node, ast.Import):
                    roots.update(alias.name.split(".")[0] for alias in node.names)
    return roots


def test_watches_every_package_the_app_imports() -> None:
    """A new runtime package must be added to the reload scope, or dev stops
    reloading on its changes."""
    runtime_packages = _imported_roots() & _local_packages()

    assert runtime_packages <= set(settings.reload_packages), (
        f"unwatched runtime packages: {sorted(runtime_packages - set(settings.reload_packages))}"
    )


def test_does_not_watch_the_tests_or_docs() -> None:
    """Editing a test or a doc must not restart the backend."""
    config = uvicorn.Config(
        "houses.server:app",
        reload=True,
        reload_dirs=list(settings.reload_packages),
    )
    watched = {path.name for path in config.reload_dirs}

    assert {"houses", "dag", "serialization"} <= watched
    assert "tests" not in watched
    assert "docs" not in watched
