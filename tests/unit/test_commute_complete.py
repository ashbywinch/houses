"""Completeness check — a killed batch must not be mistaken for a finished one."""

from __future__ import annotations

from tools.commute.station_shed import is_complete


def test_not_complete_when_existing_is_none():
    assert is_complete(None, [], 150) is False


def test_not_complete_when_short_of_expected():
    # Killed at 125 of 150 expected stations: resume must continue, not declare done.
    existing = [{"crs": f"X{i}"} for i in range(125)]
    assert is_complete(existing, existing, 150) is False


def test_not_complete_when_run_did_new_work():
    existing = [{"crs": f"X{i}"} for i in range(125)]
    records = [{"crs": f"X{i}"} for i in range(150)]
    assert is_complete(existing, records, 150) is False


def test_complete_when_all_expected_done_and_no_new_work():
    existing = [{"crs": f"X{i}"} for i in range(150)]
    assert is_complete(existing, existing, 150) is True
