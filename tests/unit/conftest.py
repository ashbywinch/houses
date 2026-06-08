"""Pytest configuration — prevents external API calls and sheet writes."""

import tempfile
from pathlib import Path

import pytest

from houses.api_cache import set_cache_dir
from houses.config import settings


@pytest.fixture(autouse=True)
def _offline_scraper():
    """Prevent the scraper from starting Chrome during tests."""
    saved = settings.rightmove_scraper_offline
    settings.rightmove_scraper_offline = True
    yield
    settings.rightmove_scraper_offline = saved


@pytest.fixture(autouse=True)
def _isolate_api_cache():
    """Isolate the disk API cache to a temp directory per test.
    Fail if any cache files appear — unit tests must not hit real APIs."""
    with tempfile.TemporaryDirectory() as tmp:
        set_cache_dir(tmp)
        yield
        files = list(Path(tmp).iterdir())
        assert not files, (
            f"Unit test created {len(files)} cache file(s), meaning it hit a real API. "
            f"Cache files: {[f.name for f in files]}"
        )


@pytest.fixture(autouse=True)
def _no_sheet_writes():
    """Prevent tests from touching a real Google Sheet."""
    saved = settings.sheet_id
    settings.sheet_id = ""
    yield
    settings.sheet_id = saved
