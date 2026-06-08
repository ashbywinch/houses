"""Integration test configuration — isolated temp cache, no sheet writes."""

import tempfile

import pytest

from houses.api_cache import set_cache_dir
from houses.config import settings


@pytest.fixture(autouse=True)
def _isolate_api_cache():
    """Isolate the disk API cache to a temp directory per test.
    Integration tests that need pre-seeded cache from tests/fixtures/api_cache/
    should set up a separate fixture that copies the needed files."""
    with tempfile.TemporaryDirectory() as tmp:
        set_cache_dir(tmp)
        yield


@pytest.fixture(autouse=True)
def _no_sheet_writes():
    """Prevent integration tests from touching a real Google Sheet."""
    saved = settings.sheet_id
    settings.sheet_id = ""
    yield
    settings.sheet_id = saved
