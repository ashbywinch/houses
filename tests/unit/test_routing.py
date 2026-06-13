"""Tests for houses/routing.py — get_commute(), _walk_commute(), etc."""

from __future__ import annotations

import pytest
from money import Money

from houses.commute import Commute, CostGroup, JourneyLeg, LegMode

# ── Fail-fast when API keys are missing ─────────────────────────────────


class TestWalkCommuteFailsFast:
    """_walk_commute must raise ValueError when Google API key is missing."""

    def test_raises_without_api_key(self):
        import asyncio

        from houses.config import settings
        from houses.routing import _walk_commute

        original = settings.google_maps_api_key
        try:
            settings.google_maps_api_key = ""
            with pytest.raises(ValueError, match="Google Maps API key not configured"):
                asyncio.run(_walk_commute("SW1V 2QQ", "EC3A 7LP"))
        finally:
            settings.google_maps_api_key = original


# ── Congestion zone ─────────────────────────────────────────────────────


class TestCongestionZone:
    """_in_congestion_zone must correctly identify central London postcodes."""

    @pytest.mark.parametrize(
        "postcode,expected",
        [
            ("SW1V 2QQ", True),  # Simon — Pimlico
            ("EC3A 7LP", True),  # Lorena — Aldgate
            ("N1 9GU", False),  # Islington — outside zone (only Angel is inside)
            ("SE1 7PB", False),  # Southwark — large parts outside zone
            ("E1 6AN", False),  # Whitechapel — outside zone
            ("RG12 8YA", False),  # Bracknell
            ("SW19 5AE", False),  # Wimbledon (outer London — NOT in zone)
            ("KT13 8XG", False),  # Weybridge
            ("NW1 4SA", False),  # Camden Town (not in zone)
            ("SL6", False),  # Maidenhead
            ("GU22 8BQ", False),  # Woking
            ("HP13", False),  # High Wycombe
        ],
    )
    def test_congestion_zone(self, postcode, expected):
        from houses.routing import _in_congestion_zone

        assert _in_congestion_zone(postcode) == expected


# ── get_commute decision logic (backends mocked) ────────────────────────

_WALK_60 = Commute(
    destination_label="", destination_postcode="", duration_minutes=60, daily_cost_gbp=Money("0.0", "GBP")
)
_WALK_20 = Commute(
    destination_label="", destination_postcode="", duration_minutes=20, daily_cost_gbp=Money("0.0", "GBP")
)
_TRANSIT_30 = Commute(
    destination_label="", destination_postcode="", duration_minutes=30, daily_cost_gbp=Money("8.0", "GBP")
)
_DRIVE_25 = Commute(
    destination_label="", destination_postcode="", duration_minutes=25, daily_cost_gbp=Money("5.0", "GBP")
)

# Tiebreak fixture — route with cost, used by test_returns_cost_when_tfl_has_cost
_SLOWER_HAS_COST = Commute(
    destination_label="", destination_postcode="", duration_minutes=25, daily_cost_gbp=Money("5.0", "GBP")
)


class TestGetCommuteChoice:
    """get_commute picks the best option among walking, transit, driving."""

    @pytest.mark.asyncio
    async def test_walking_wins_when_fastest(self, monkeypatch):
        """Walking within max_walk_minutes should be returned immediately."""
        from houses.routing import get_commute

        async def mock_walk(*_):
            return _WALK_20

        async def mock_transit(*_, **__):
            return _TRANSIT_30

        async def mock_none(*_, **__):
            return None

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

        async def mock_none(*_, **__):
            return None

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

        async def mock_none(*_, **__):
            return None

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

        async def mock_none(*_, **__):
            return None

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

        async def mock_none(*_, **__):
            return None

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

        async def mock_none(*_, **__):
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

    # ── Tiebreak: priced vs non-priced routes ─────────────────────────
    # Requirement: "Have an accurate price for the whole journey" (#1).
    # When Google Routes returns a faster route without cost data and TfL
    # has a slightly slower route with a real cost, prefer TfL.  The NR
    # fare fallback can only approximate a rail fare — a real TfL cost
    # is more accurate.

    @pytest.mark.asyncio
    async def test_returns_cost_when_tfl_has_cost(self, monkeypatch):
        """TfL returns a route with cost → it's selected."""
        from houses.routing import get_commute

        async def mock_walk(*_):
            return _WALK_60

        async def mock_tfl(*_, **__):
            return _SLOWER_HAS_COST  # 25 min, cost=5.0

        monkeypatch.setattr("houses.routing._walk_commute", mock_walk)
        monkeypatch.setattr("houses.routing._tfl_transit_commute", mock_tfl)

        result = await get_commute("GU21 7QF", "EC3A 7LP", has_car=False, max_walk_minutes=30)
        assert result.is_succeeded, f"Expected succeeded, got {result}"
        best = result.value_or_none()
        assert best.daily_cost_gbp == Money("5.0", "GBP"), "Should return the route with a real cost"


# ── TfL: no bus when has_car=True ────────────────────────────────────


class TestTflNoBusWhenHasCar:
    """_tfl_transit_commute skips with_bus when no_bus succeeds."""

    @pytest.mark.asyncio
    async def test_skips_with_bus_when_no_bus_succeeds(self, monkeypatch):
        """has_car=True + no_bus succeeds → with_bus is not compared."""
        from houses.attempt import Attempt
        from houses.commute import Commute

        no_bus = Commute(
            destination_label="",
            destination_postcode="SW1V 2QQ",
            duration_minutes=90,
            daily_cost_gbp=Money("20.0", "GBP"),
        )
        with_bus = Commute(
            destination_label="",
            destination_postcode="SW1V 2QQ",
            duration_minutes=70,
            daily_cost_gbp=Money("15.0", "GBP"),
        )

        call_count = 0

        async def mock_plan(self):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return Attempt.succeeded(no_bus, "tfl")
            return Attempt.succeeded(with_bus, "tfl")

        from houses.routing import _tfl_transit_commute

        monkeypatch.setattr("houses.transit_route.TransitRoute.plan", mock_plan)

        result = await _tfl_transit_commute("GU21 2NA", "EC3A 7LP", has_car=True)
        assert result is not None
        assert result.duration_minutes == 90, (
            f"Expected no_bus (90 min), got {result.duration_minutes} — with_bus was compared when no_bus succeeded"
        )

    @pytest.mark.asyncio
    async def test_uses_with_bus_when_no_bus_fails(self, monkeypatch):
        """has_car=True + no_bus fails → with_bus is used as last resort."""
        from houses.attempt import Attempt
        from houses.commute import Commute

        no_bus = Attempt.impossible("tfl", "no route found")
        with_bus = Commute(
            destination_label="",
            destination_postcode="SW1V 2QQ",
            duration_minutes=70,
            daily_cost_gbp=Money("15.0", "GBP"),
        )

        call_count = 0

        async def mock_plan(self):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return no_bus
            return Attempt.succeeded(with_bus, "tfl")

        from houses.routing import _tfl_transit_commute

        monkeypatch.setattr("houses.transit_route.TransitRoute.plan", mock_plan)

        result = await _tfl_transit_commute("GU21 2NA", "EC3A 7LP", has_car=True)
        assert result is not None
        assert result.duration_minutes == 70, f"Expected with_bus (70 min) as fallback, got {result.duration_minutes}"


# ── Park-and-ride creates parking CostGroup ─────────────────────────


class TestParkAndRideCostGroup:
    """_add_parking_cost must return a CostGroup with parking cost so
    ``Simon Parking Cost (£)`` (derived from ``non_rail_cost()``) shows
    the real parking fee, not bus fares."""

    @pytest.mark.asyncio
    async def test_returns_parking_cost_group(self):
        """_add_parking_cost returns a parking CostGroup with cost, operator='ParkCo'."""
        from money import Money

        from houses.car_park import CarPark, CarParkRegistry
        from houses.transit_route import TransitRoute

        registry = CarParkRegistry.from_car_parks(
            car_parks=[CarPark(name="Fleet", daily_cost=Money("10.90", "GBP"))],
            station_map={"fleet rail station": "Fleet"},
        )

        route = TransitRoute("SL6", "SW1V 2QQ", "test", park_and_ride=True)
        data = {
            "journeys": [
                {
                    "duration": 87,
                    "legs": [
                        {
                            "mode": {"name": "driving"},
                            "duration": 15,
                            "isTimeline": True,
                            "arrivalPoint": {"commonName": "Fleet Rail Station"},
                        },
                        {"mode": {"name": "train", "isTimeline": True}, "duration": 30},
                    ],
                    "fare": {"totalCost": 500, "singleFare": 250},
                }
            ]
        }

        parking_cost, new_cost, parking_groups = await route._add_parking_cost(data, 30.0, _registry=registry)

        assert parking_cost == 10.90, f"Expected 10.90, got {parking_cost}"
        assert new_cost == 40.90, f"Expected 40.90, got {new_cost}"
        assert len(parking_groups) == 1, "Expected one parking CostGroup"
        assert parking_groups[0].cost == Money("10.90", "GBP"), (
            f"Parking CostGroup should have cost=Money('10.90', 'GBP'), got {parking_groups[0].cost}"
        )
        assert parking_groups[0].legs[0].mode == LegMode.PARK, "Parking CostGroup should have LegMode.PARK"


# ── School commute ──────────────────────────────────────────────────────


class TestSchoolCommute:
    """compute_school_commute — thin wrapper around get_commute."""

    @pytest.mark.asyncio
    async def test_delegates_to_get_commute(self, monkeypatch):
        """compute_school_commute calls get_commute with has_car=False, max_walk_minutes=20."""
        from houses.schools import School, SchoolGender, compute_school_commute

        captured = {}

        async def mock_get_commute(origin, dest, *, has_car, max_walk_minutes):
            captured.update(origin=origin, dest=dest, has_car=has_car, max_walk_minutes=max_walk_minutes)
            from houses.attempt import Attempt

            commute = Commute(
                destination_label="",
                destination_postcode=dest,
                duration_minutes=10,
                daily_cost_gbp=Money("0.0", "GBP"),
            )
            return Attempt.succeeded(commute, "test")

        monkeypatch.setattr("houses.routing.get_commute", mock_get_commute)

        school = School(
            urn="123456",
            name="Test",
            phase="Primary",
            gender=SchoolGender.MIXED,
            type_of_establishment="Community School",
            postcode="SL6 1AA",
            website="",
            ofsted_rating="",
            inspection_year="",
            coords=None,
            statutory_low_age=None,
            statutory_high_age=None,
        )
        result = await compute_school_commute("SL6 1AA", school)

        assert result is not None
        assert result.duration_minutes == 10
        assert captured["has_car"] is False
        assert captured["max_walk_minutes"] == 20
        assert captured["origin"] == "SL6 1AA"
        assert captured["dest"] == "SL6 1AA"


# ── _replace_walk_with_bus ──────────────────────────────────────────────


def _tfl_complete(duration=90, cost="12.50", walk=46) -> Commute:
    """A TfL commute with walk + train + tube legs and full cost."""
    return Commute(
        destination_label="L",
        destination_postcode="EC3A 7LP",
        duration_minutes=duration,
        daily_cost_gbp=Money(cost, "GBP"),
        cost_groups=(
            CostGroup(legs=(JourneyLeg(mode=LegMode.WALK, duration_minutes=walk),)),
            CostGroup(legs=(JourneyLeg(mode=LegMode.TRAIN, duration_minutes=42),)),
            CostGroup(legs=(JourneyLeg(mode=LegMode.TUBE, duration_minutes=4),)),
        ),
    )


def _bus_route() -> Commute:
    """A bus route that saves 8 min of walking for £3.80."""
    return Commute(
        destination_label="L (Bus)",
        destination_postcode="EC3A 7LP",
        duration_minutes=55,
        daily_cost_gbp=Money("3.80", "GBP"),
        cost_groups=(
            CostGroup(
                legs=(JourneyLeg(mode=LegMode.BUS, duration_minutes=28),),
                cost=3.80,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_replace_walk_with_bus_short_walk():
    """When walk is shorter than penalty, the TfL commute is returned unchanged."""
    from houses.routing import _replace_walk_with_bus

    original = _tfl_complete(walk=5)
    result = await _replace_walk_with_bus(original, "GU22 8RU", "EC3A 7LP", 5)
    assert result is original
    assert result.daily_cost_gbp == Money("12.50", "GBP")


@pytest.mark.asyncio
async def test_replace_walk_with_bus_no_bus():
    """When no bus is available, the TfL commute is returned unchanged."""
    from houses.routing import _replace_walk_with_bus

    original = _tfl_complete(walk=46)
    result = await _replace_walk_with_bus(
        original, "GU22 8RU", "EC3A 7LP", 46, _bus_alternative=None
    )
    assert result is original


@pytest.mark.asyncio
async def test_replace_walk_with_bus_replaces_walk():
    """When the bus is viable, walking time is replaced and bus cost added."""
    from houses.routing import _replace_walk_with_bus

    original = _tfl_complete(duration=90, cost="12.50", walk=46)
    result = await _replace_walk_with_bus(
        original, "GU22 8RU", "EC3A 7LP", 46, _bus_alternative=_bus_route()
    )
    # Duration: 90 - 46 + min(15, 46-10=36) = 90 - 46 + 15 = 59
    assert result.duration_minutes == 59
    # Cost: TfL £12.50 + bus £3.80 = £16.30
    assert result.daily_cost_gbp == Money("16.30", "GBP")


@pytest.mark.asyncio
async def test_replace_walk_with_bus_short_walk_no_replace():
    """When walk is under the penalty threshold, no replacement is tried even with a bus."""
    from houses.routing import _replace_walk_with_bus

    original = _tfl_complete(duration=90, cost="12.50", walk=9)
    result = await _replace_walk_with_bus(
        original, "GU22 8RU", "EC3A 7LP", 9, _bus_alternative=_bus_route()
    )
    assert result is original
