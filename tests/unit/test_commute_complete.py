"""Completeness check — a killed batch must not be mistaken for a finished one."""

from __future__ import annotations

from houses.geo import GeoPoint
from tools.commute.station_shed import (
    Office,
    build_metadata,
    config_signature,
    is_complete,
    resume_allowed,
)

SIG = config_signature([Office("SW1V 2QQ", GeoPoint(51.4904, -0.1378)), Office("EC3A 7LP", GeoPoint(51.5145, -0.0762))])


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


def test_not_complete_when_records_replaced_stale_entry():
    # A resume that replaced a stale record (changed coords) has equal length
    # but different content — it must not be reported as "already complete"
    # or the replacement is never written to disk.
    existing = [
        {"crs": "X1", "lat": 51.0, "lon": -0.9, "kept": True},
        {"crs": "X2", "lat": 52.0, "lon": 0.0, "kept": True},
    ]
    records = [
        {"crs": "X1", "lat": 51.0, "lon": -0.9, "kept": True},
        {"crs": "X2", "lat": 52.1, "lon": 0.1, "kept": True},
    ]
    assert is_complete(existing, records, 2) is False


def test_not_complete_when_any_record_failed():
    # Failed routes are unfinished work — a resume must re-route them, so an
    # all-done-looking shed with a routing_error is never "already complete".
    existing = [
        {"crs": "X1", "lat": 51.0, "lon": -0.9, "kept": True},
        {"crs": "X2", "lat": 52.0, "lon": 0.0, "kept": False, "routing_error": "failed"},
    ]
    assert is_complete(existing, existing, 2) is False


def test_resume_allowed_matching_config():
    assert resume_allowed(SIG, SIG) is True


def test_resume_allowed_rejects_engine_version_mismatch():
    assert resume_allowed(dict(SIG, engine_version="station-shed-v0"), SIG) is False


def test_resume_allowed_rejects_destination_change():
    # Changing an office postcode without a version bump must refuse a resume
    # (records routed to the old destination would mix with new metadata).
    assert resume_allowed(dict(SIG, destinations=["SW1V 2QQ", "EC2A 4BX"]), SIG) is False


def test_resume_allowed_rejects_threshold_change():
    assert resume_allowed(dict(SIG, threshold_min=105), SIG) is False


def test_resume_allowed_rejects_missing_config():
    assert resume_allowed({}, SIG) is False


def test_build_metadata_uses_current_constants_and_keeps_timestamp():
    offices = [Office("SW1V 2QQ", GeoPoint(51.4904, -0.1378)), Office("EC3A 7LP", GeoPoint(51.5145, -0.0762))]
    meta = build_metadata(offices, 1819, "2026-08-02T09:00:00+00:00")
    assert meta["generated_at"] == "2026-08-02T09:00:00+00:00"  # preserved across resumes
    assert meta["threshold_min"] == 132  # current constants, never stale
    assert meta["destinations"] == ["SW1V 2QQ", "EC3A 7LP"]
    assert meta["expected_stations"] == 1819
    assert meta["engine_version"] == "station-shed-v1"
