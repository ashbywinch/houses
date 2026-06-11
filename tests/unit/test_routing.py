"""Tests for houses/routing.py — get_commute(), _walk_commute(), etc."""

from __future__ import annotations

import pytest

from houses.commute import Commute


# ── Fail-fast when API keys are missing ─────────────────────────────────


class TestWalkCommuteFailsFast:
    """_walk_commute must raise ValueError when Google API key is missing."""

    def test_raises_without_api_key(self):
        from houses.routing import _GoogleRoutesClient, _walk_commute

        import asyncio
        from houses.config import settings

        _GoogleRoutesClient._blocked = False  # reset from previous tests
        original = settings.google_maps_api_key
        try:
            settings.google_maps_api_key = ""
            with pytest.raises(ValueError, match="Google Maps API key not configured"):
                asyncio.run(_walk_commute("SW1V 2QQ", "EC3A 7LP"))
        finally:
            settings.google_maps_api_key = original


class TestGoogleTransitCommuteFailsFast:
    """_google_transit_commute must raise ValueError when Google API key is missing."""

    def test_raises_without_api_key(self):
        from houses.routing import _GoogleRoutesClient, _google_transit_commute

        import asyncio
        from houses.config import settings

        _GoogleRoutesClient._blocked = False  # reset from previous tests
        original = settings.google_maps_api_key
        try:
            settings.google_maps_api_key = ""
            with pytest.raises(ValueError, match="Google Maps API key not configured"):
                asyncio.run(_google_transit_commute("SW1V 2QQ", "EC3A 7LP"))
        finally:
            settings.google_maps_api_key = original


class TestDriveCommuteFailsFast:
    """_drive_commute must raise ValueError when ORS API key is missing."""

    def test_raises_without_api_key(self):
        from houses.routing import _drive_commute

        import asyncio
        from houses.config import settings

        original = settings.ors_api_key
        try:
            settings.ors_api_key = ""
            with pytest.raises(ValueError, match="ORS API key not configured"):
                asyncio.run(_drive_commute("SW1V 2QQ", "EC3A 7LP"))
        finally:
            settings.ors_api_key = original


# ── Congestion zone ─────────────────────────────────────────────────────


class TestCongestionZone:
    """_in_congestion_zone must correctly identify central London postcodes."""

    @pytest.mark.parametrize(
        "postcode,expected",
        [
            ("SW1V 2QQ", True),   # Simon — Pimlico
            ("EC3A 7LP", True),   # Lorena — Aldgate
            ("N1 9GU", True),     # Islington
            ("SE1 7PB", True),    # Southwark
            ("E1 6AN", True),     # Whitechapel
            ("RG12 8YA", False),  # Bracknell
            ("SW19 5AE", False),  # Wimbledon (outer London — NOT in zone)
            ("KT13 8XG", False),  # Weybridge
            ("NW1 4SA", False),   # Camden Town (not in zone)
            ("SL6", False),       # Maidenhead
            ("GU22 8BQ", False),  # Woking
            ("HP13", False),      # High Wycombe
        ],
    )
    def test_congestion_zone(self, postcode, expected):
        from houses.routing import _in_congestion_zone

        assert _in_congestion_zone(postcode) == expected


# ── get_commute decision logic (backends mocked) ────────────────────────

_WALK_60 = Commute(
    destination_label="", destination_postcode="", duration_minutes=60, daily_cost_gbp=0.0
)
_WALK_20 = Commute(
    destination_label="", destination_postcode="", duration_minutes=20, daily_cost_gbp=0.0
)
_TRANSIT_30 = Commute(
    destination_label="", destination_postcode="", duration_minutes=30, daily_cost_gbp=8.0
)
_DRIVE_25 = Commute(
    destination_label="", destination_postcode="", duration_minutes=25, daily_cost_gbp=5.0
)


def _future(c):
    import asyncio

    return asyncio.Future() if False else c  # placeholder — monkeypatch replaces the function


class TestGetCommuteChoice:
    """get_commute picks the fastest option among walking, transit, driving."""

    @pytest.fixture(autouse=True)
    def _reset_blocked(self):
        from houses.routing import _GoogleRoutesClient

        _GoogleRoutesClient._blocked = False

    @pytest.mark.asyncio
    async def test_walking_wins_when_fastest(self, monkeypatch):
        """Walking within max_walk_minutes should be returned immediately."""
        from houses.routing import get_commute

        async def mock_walk(*_):
            return _WALK_20

        async def mock_transit(*_, **__):
            return _TRANSIT_30

        monkeypatch.setattr("houses.routing._walk_commute", mock_walk)
        monkeypatch.setattr("houses.routing._tfl_transit_commute", mock_transit)

        result = await get_commute("GU21 7QF", "SW1V 2QQ", has_car=False, max_walk_minutes=30)
        assert result.is_succeeded, f"Expected succeeded, got {result}"
        assert result.value_or_none().duration_minutes == 20

    @pytest.mark.asyncio
    async def test_walking_skipped_when_too_slow(self, monkeypatch):
        """Walking longer than max_walk_minutes should fall through to transit."""
        from houses.routing import get_commute

        async def mock_walk(*_):
            return _WALK_60

        async def mock_transit(*_, **__):
            return _TRANSIT_30

        monkeypatch.setattr("houses.routing._walk_commute", mock_walk)
        monkeypatch.setattr("houses.routing._tfl_transit_commute", mock_transit)

        result = await get_commute("GU21 7QF", "SW1V 2QQ", has_car=False, max_walk_minutes=30)
        assert result.is_succeeded, f"Expected succeeded, got {result}"
        assert result.value_or_none().duration_minutes == 30  # transit, not walking

    @pytest.mark.asyncio
    async def test_driving_considered_when_has_car(self, monkeypatch):
        """With has_car=True and no congestion zone, driving should be an option."""
        from houses.routing import get_commute

        async def mock_walk(*_):
            return _WALK_60

        async def mock_transit(*_, **__):
            return None  # no transit available

        async def mock_drive(*_):
            return _DRIVE_25

        def mock_cz(_):
            return False

        monkeypatch.setattr("houses.routing._walk_commute", mock_walk)
        monkeypatch.setattr("houses.routing._tfl_transit_commute", mock_transit)
        monkeypatch.setattr("houses.routing._drive_commute", mock_drive)
        monkeypatch.setattr("houses.routing._in_congestion_zone", mock_cz)

        result = await get_commute("GU21 7QF", "RG12 8YA", has_car=True, max_walk_minutes=15)
        assert result.is_succeeded, f"Expected succeeded, got {result}"
        assert result.value_or_none().duration_minutes == 25  # driving

    @pytest.mark.asyncio
    async def test_prefers_faster_of_transit_and_drive(self, monkeypatch):
        """With both transit and driving available, picks the faster one."""
        from houses.routing import get_commute

        async def mock_walk(*_):
            return _WALK_60

        async def mock_transit(*_, **__):
            return _TRANSIT_30

        async def mock_drive(*_):
            return _DRIVE_25

        def mock_cz(_):
            return False

        monkeypatch.setattr("houses.routing._walk_commute", mock_walk)
        monkeypatch.setattr("houses.routing._tfl_transit_commute", mock_transit)
        monkeypatch.setattr("houses.routing._drive_commute", mock_drive)
        monkeypatch.setattr("houses.routing._in_congestion_zone", mock_cz)

        result = await get_commute("GU21 7QF", "RG12 8YA", has_car=True, max_walk_minutes=15)
        assert result.is_succeeded, f"Expected succeeded, got {result}"
        assert result.value_or_none().duration_minutes == 25  # driving

    @pytest.mark.asyncio
    async def test_prefers_faster_of_transit_and_drive(self, monkeypatch):
        """With both transit and driving available, picks the faster one."""
        from houses.routing import get_commute

        async def mock_walk(*_):
            return _WALK_60

        async def mock_transit(*_, **__):
            return _TRANSIT_30

        async def mock_drive(*_):
            return _DRIVE_25

        def mock_cz(_):
            return False

        monkeypatch.setattr("houses.routing._walk_commute", mock_walk)
        monkeypatch.setattr("houses.routing._tfl_transit_commute", mock_transit)
        monkeypatch.setattr("houses.routing._drive_commute", mock_drive)
        monkeypatch.setattr("houses.routing._in_congestion_zone", mock_cz)

        result = await get_commute("GU21 7QF", "RG12 8YA", has_car=True, max_walk_minutes=15)
        assert result.is_succeeded, f"Expected succeeded, got {result}"
        assert result.value_or_none().duration_minutes == 25  # driving is faster than transit

    @pytest.mark.asyncio
    async def test_skips_driving_for_congestion_zone(self, monkeypatch):
        """Central London destinations should never try driving."""
        from houses.routing import get_commute

        async def mock_walk(*_):
            return None

        async def mock_transit(*_, **__):
            return _TRANSIT_30

        async def mock_drive(*_):
            return _DRIVE_25

        def mock_cz(_):
            return True

        monkeypatch.setattr("houses.routing._walk_commute", mock_walk)
        monkeypatch.setattr("houses.routing._tfl_transit_commute", mock_transit)
        monkeypatch.setattr("houses.routing._drive_commute", mock_drive)
        monkeypatch.setattr("houses.routing._in_congestion_zone", mock_cz)

        result = await get_commute("GU21 7QF", "SW1V 2QQ", has_car=True, max_walk_minutes=15)
        assert result.is_succeeded, f"Expected succeeded, got {result}"
        assert result.value_or_none().duration_minutes == 30  # transit, not driving

    @pytest.mark.asyncio
    async def test_returns_impossible_when_no_route(self, monkeypatch):
        """When all backends return None, get_commute returns Attempt.impossible."""
        from houses.routing import get_commute

        async def mock_walk(*_):
            return None

        async def mock_transit(*_, **__):
            return None

        async def mock_drive(*_):
            return None

        def mock_cz(_):
            return False

        monkeypatch.setattr("houses.routing._walk_commute", mock_walk)
        monkeypatch.setattr("houses.routing._tfl_transit_commute", mock_transit)
        monkeypatch.setattr("houses.routing._drive_commute", mock_drive)
        monkeypatch.setattr("houses.routing._in_congestion_zone", mock_cz)

        result = await get_commute("GU21 7QF", "RG12 8YA", has_car=True, max_walk_minutes=15)
        assert result.is_impossible, f"Expected impossible, got {result}"
        assert result.reason, "Should have a reason for failure"


# ── School commute ──────────────────────────────────────────────────────


class TestSchoolCommute:
    """compute_school_commute — thin wrapper around get_commute."""

    @pytest.fixture(autouse=True)
    def _reset_blocked(self):
        from houses.routing import _GoogleRoutesClient

        _GoogleRoutesClient._blocked = False

    @pytest.mark.asyncio
    async def test_delegates_to_get_commute(self, monkeypatch):
        """compute_school_commute calls get_commute with has_car=False, max_walk_minutes=20."""
        from houses.schools import School, SchoolGender, compute_school_commute

        captured = {}

        async def mock_get_commute(origin, dest, *, has_car, max_walk_minutes):
            captured.update(origin=origin, dest=dest, has_car=has_car, max_walk_minutes=max_walk_minutes)
            from houses.attempt import Attempt
            return Attempt.succeeded(Commute(destination_label="", destination_postcode=dest, duration_minutes=10, daily_cost_gbp=0.0), "test")

        monkeypatch.setattr("houses.routing.get_commute", mock_get_commute)

        school = School(
            urn="123456", name="Test", phase="Primary", gender=SchoolGender.MIXED,
            type_of_establishment="Community School", postcode="SL6 1AA",
            website="", ofsted_rating="", inspection_year="",
            coords=None, statutory_low_age=None, statutory_high_age=None,
        )
        result = await compute_school_commute("SL6 1AA", school)

        assert result is not None
        assert result.duration_minutes == 10
        assert captured["has_car"] is False
        assert captured["max_walk_minutes"] == 20
        assert captured["origin"] == "SL6 1AA"
        assert captured["dest"] == "SL6 1AA"




