"""Pytest configuration — prevents tests from writing to any Google Sheet."""

import pytest

from houses.config import settings


@pytest.fixture(autouse=True)
def _no_sheet_writes():
    """Ensure tests never touch a real Google Sheet.

    By clearing sheet_id, write_enriched_row returns None immediately
    and the server responds with 200 + data inline instead of 201.
    """
    saved = settings.sheet_id
    settings.sheet_id = ""
    yield
    settings.sheet_id = saved
