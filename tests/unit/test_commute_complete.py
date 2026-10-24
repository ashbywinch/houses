"""Completeness check — a killed batch must not be mistaken for a finished one."""

from __future__ import annotations

import json

from houses.geopoint import GeoPoint
from tools.commute.station_shed import (
    Office,
    ShedMetadata,
    ShedPayload,
    ShedRecord,
    _ShedFile,
    build_metadata,
    config_signature,
    is_complete,
    resume_allowed,
)

_OFFICES = [Office("SW1P 1AA", GeoPoint(51.4904, -0.1378)), Office("EC3A 4NE", GeoPoint(51.5145, -0.0762))]
SIG = config_signature(_OFFICES)


def _rec(crs: str, *, lat: float = 51.0, lon: float = -0.9, kept: bool = True, error: str | None = None) -> ShedRecord:
    """A shed record with only the fields a test cares about varied."""
    return ShedRecord(
        name=crs,
        crs=crs,
        lat=lat,
        lon=lon,
        duration_pimlico=30,
        duration_aldgate=30,
        kept=kept,
        routing_error=error,
    )


def test_not_complete_when_existing_is_none():
    assert is_complete(None, [], 150) is False


def test_not_complete_when_short_of_expected():
    # Killed at 125 of 150 expected stations: resume must continue, not declare done.
    existing = [_rec(f"X{i}") for i in range(125)]
    assert is_complete(existing, existing, 150) is False


def test_not_complete_when_run_did_new_work():
    existing = [_rec(f"X{i}") for i in range(125)]
    records = [_rec(f"X{i}") for i in range(150)]
    assert is_complete(existing, records, 150) is False


def test_complete_when_all_expected_done_and_no_new_work():
    existing = [_rec(f"X{i}") for i in range(150)]
    assert is_complete(existing, existing, 150) is True


def test_not_complete_when_records_replaced_stale_entry():
    # A resume that replaced a stale record (changed coords) has equal length
    # but different content — it must not be reported as "already complete"
    # or the replacement is never written to disk.
    existing = [_rec("X1"), _rec("X2", lat=52.0, lon=0.0)]
    records = [_rec("X1"), _rec("X2", lat=52.1, lon=0.1)]
    assert is_complete(existing, records, 2) is False


def test_not_complete_when_any_record_failed():
    # Failed routes are unfinished work — a resume must re-route them, so an
    # all-done-looking shed with a routing_error is never "already complete".
    existing = [_rec("X1"), _rec("X2", lat=52.0, lon=0.0, kept=False, error="failed")]
    assert is_complete(existing, existing, 2) is False


def _stored_metadata(**overrides) -> ShedMetadata:
    """The metadata a stored shed carries, with one field changed — what
    ``resume_allowed`` actually compares a signature against."""
    return build_metadata(_OFFICES, 0, "2026-08-02T09:00:00+00:00").model_copy(update=overrides)


def test_resume_allowed_matching_config():
    assert resume_allowed(_stored_metadata(), SIG) is True

def test_resume_allowed_rejects_engine_version_mismatch():
    assert resume_allowed(_stored_metadata(engine_version="station-shed-v0"), SIG) is False


def test_resume_allowed_rejects_destination_change():
    # Changing an office postcode without a version bump must refuse a resume
    # (records routed to the old destination would mix with new metadata).
    assert resume_allowed(_stored_metadata(destinations=["SW1P 1AA", "EC2A 4BX"]), SIG) is False


def test_resume_allowed_rejects_threshold_change():
    assert resume_allowed(_stored_metadata(threshold_min=105), SIG) is False


def test_build_metadata_uses_current_constants_and_keeps_timestamp():
    offices = [Office("SW1P 1AA", GeoPoint(51.4904, -0.1378)), Office("EC3A 4NE", GeoPoint(51.5145, -0.0762))]
    meta = build_metadata(offices, 1819, "2026-08-02T09:00:00+00:00")
    assert meta.generated_at == "2026-08-02T09:00:00+00:00"  # preserved across resumes
    assert meta.threshold_min == 132  # current constants, never stale
    assert meta.destinations == ["SW1P 1AA", "EC3A 4NE"]
    assert meta.expected_stations == 1819
    assert meta.engine_version == "station-shed-v1"


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
def test_unusable_shed_metadata_starts_fresh(tmp_path):
    """A truncated or hand-edited shed — metadata missing a config key — must
    start a fresh batch, never wedge the tool (the records cannot be trusted)."""
    path = tmp_path / "shed.json"
    path.write_text(json.dumps({"metadata": {"engine_version": "station-shed-v1"}, "stations": []}))

    state = _ShedFile(path, _OFFICES).resume_or_wipe(force=False)

    assert state.existing is None
    assert state.abort is None


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
def test_shed_built_under_another_config_refuses_to_resume(tmp_path):
    """A shed routed to different offices/thresholds must not be resumed —
    its records would mix with metadata claiming the current config."""
    path = tmp_path / "shed.json"
    stale = build_metadata(_OFFICES, 0, "2026-08-02T09:00:00+00:00").model_copy(
        update={"engine_version": "station-shed-v0"},
    )
    path.write_text(json.dumps(ShedPayload(metadata=stale, stations=[]).model_dump()))

    state = _ShedFile(path, _OFFICES).resume_or_wipe(force=False)

    assert state.abort == 1
    assert state.existing is None
