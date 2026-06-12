"""Tests for houses/routing.py — get_commute(), _walk_commute(), etc."""

from __future__ import annotations

import pytest

from houses.commute import Commute, JourneyLeg, LegMode

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


class TestGoogleTransitCommuteFailsFast:
    """_google_transit_commute must raise ValueError when Google API key is missing."""

    def test_raises_without_api_key(self):
        import asyncio

        from houses.config import settings
        from houses.routing import _google_transit_commute

        original = settings.google_maps_api_key
        try:
            settings.google_maps_api_key = ""
            with pytest.raises(ValueError, match="Google Maps API key not configured"):
                asyncio.run(_google_transit_commute("SW1V 2QQ", "EC3A 7LP"))
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
            ("N1 9GU", True),  # Islington
            ("SE1 7PB", True),  # Southwark
            ("E1 6AN", True),  # Whitechapel
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

_WALK_60 = Commute(destination_label="", destination_postcode="", duration_minutes=60, daily_cost_gbp=0.0)
_WALK_20 = Commute(destination_label="", destination_postcode="", duration_minutes=20, daily_cost_gbp=0.0)
_TRANSIT_30 = Commute(destination_label="", destination_postcode="", duration_minutes=30, daily_cost_gbp=8.0)
_DRIVE_25 = Commute(destination_label="", destination_postcode="", duration_minutes=25, daily_cost_gbp=5.0)

# Tiebreak fixtures — routes with various cost/duration profiles
_FASTER_NO_COST = Commute(destination_label="", destination_postcode="", duration_minutes=20, daily_cost_gbp=None)
_SLOWER_HAS_COST = Commute(destination_label="", destination_postcode="", duration_minutes=25, daily_cost_gbp=5.0)
_FASTER_HAS_COST = Commute(destination_label="", destination_postcode="", duration_minutes=18, daily_cost_gbp=5.0)
_SLOWER_NO_COST = Commute(destination_label="", destination_postcode="", duration_minutes=30, daily_cost_gbp=None)
_SAME_DURATION_HAS_COST = Commute(
    destination_label="",
    destination_postcode="",
    duration_minutes=20,
    daily_cost_gbp=5.0,
)


def _future(c):
    import asyncio

    return asyncio.Future() if False else c  # placeholder — monkeypatch replaces the function


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
        monkeypatch.setattr("houses.routing._google_transit_commute", mock_none)

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
        monkeypatch.setattr("houses.routing._google_transit_commute", mock_none)

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
        monkeypatch.setattr("houses.routing._google_transit_commute", mock_none)
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
        monkeypatch.setattr("houses.routing._google_transit_commute", mock_none)
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
        monkeypatch.setattr("houses.routing._google_transit_commute", mock_none)
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
        monkeypatch.setattr("houses.routing._google_transit_commute", mock_none)
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
    async def test_returns_cost_when_google_no_cost_tfl_has_cost(self, monkeypatch):
        """Google returns a route without pricing, TfL has one with cost → priced wins."""
        from houses.routing import get_commute

        async def mock_walk(*_):
            return _WALK_60

        async def mock_google(*_, **__):
            return _FASTER_NO_COST  # 20 min, cost=None

        async def mock_tfl(*_, **__):
            return _SLOWER_HAS_COST  # 25 min, cost=5.0

        monkeypatch.setattr("houses.routing._walk_commute", mock_walk)
        monkeypatch.setattr("houses.routing._google_transit_commute", mock_google)
        monkeypatch.setattr("houses.routing._tfl_transit_commute", mock_tfl)

        result = await get_commute("GU21 7QF", "EC3A 7LP", has_car=False, max_walk_minutes=30)
        assert result.is_succeeded, f"Expected succeeded, got {result}"
        best = result.value_or_none()
        assert best.daily_cost_gbp == 5.0, "Should return the route with a real cost"

    @pytest.mark.asyncio
    async def test_returns_fastest_when_both_have_cost(self, monkeypatch):
        """Both routes with pricing → faster one wins."""
        from houses.routing import get_commute

        async def mock_walk(*_):
            return _WALK_60

        async def mock_google(*_, **__):
            return _FASTER_HAS_COST  # 18 min, cost=5.0

        async def mock_tfl(*_, **__):
            return _SLOWER_HAS_COST  # 25 min, cost=5.0

        monkeypatch.setattr("houses.routing._walk_commute", mock_walk)
        monkeypatch.setattr("houses.routing._google_transit_commute", mock_google)
        monkeypatch.setattr("houses.routing._tfl_transit_commute", mock_tfl)

        result = await get_commute("GU21 7QF", "EC3A 7LP", has_car=False, max_walk_minutes=30)
        assert result.is_succeeded, f"Expected succeeded, got {result}"
        assert result.value_or_none().duration_minutes == 18

    @pytest.mark.asyncio
    async def test_returns_fastest_when_neither_has_cost(self, monkeypatch):
        """Neither route has pricing → faster wins."""
        from houses.routing import get_commute

        async def mock_walk(*_):
            return _WALK_60

        async def mock_google(*_, **__):
            return _FASTER_NO_COST  # 20 min, cost=None

        async def mock_tfl(*_, **__):
            return _SLOWER_NO_COST  # 30 min, cost=None

        monkeypatch.setattr("houses.routing._walk_commute", mock_walk)
        monkeypatch.setattr("houses.routing._google_transit_commute", mock_google)
        monkeypatch.setattr("houses.routing._tfl_transit_commute", mock_tfl)

        result = await get_commute("GU21 7QF", "EC3A 7LP", has_car=False, max_walk_minutes=30)
        assert result.is_succeeded, f"Expected succeeded, got {result}"
        assert result.value_or_none().duration_minutes == 20

    @pytest.mark.asyncio
    async def test_returns_cost_when_same_duration(self, monkeypatch):
        """Same duration routes → the priced one wins."""
        from houses.routing import get_commute

        async def mock_walk(*_):
            return _WALK_60

        async def mock_google(*_, **__):
            return _FASTER_NO_COST  # 20 min, cost=None

        async def mock_tfl(*_, **__):
            return _SAME_DURATION_HAS_COST  # 20 min, cost=5.0

        monkeypatch.setattr("houses.routing._walk_commute", mock_walk)
        monkeypatch.setattr("houses.routing._google_transit_commute", mock_google)
        monkeypatch.setattr("houses.routing._tfl_transit_commute", mock_tfl)

        result = await get_commute("GU21 7QF", "EC3A 7LP", has_car=False, max_walk_minutes=30)
        assert result.is_succeeded, f"Expected succeeded, got {result}"
        assert result.value_or_none().daily_cost_gbp == 5.0

    @pytest.mark.asyncio
    async def test_skips_tfl_when_google_has_pricing(self, monkeypatch):
        """Google with pricing → TfL should NOT be called (minimise API usage)."""
        from houses.routing import get_commute

        tfl_called = False

        async def mock_walk(*_):
            return _WALK_60

        async def mock_google(*_, **__):
            return _FASTER_HAS_COST  # 18 min, cost=5.0

        async def mock_tfl(*_, **__):
            nonlocal tfl_called
            tfl_called = True
            return _SLOWER_HAS_COST  # 25 min, cost=5.0 (shouldn't be called)

        monkeypatch.setattr("houses.routing._walk_commute", mock_walk)
        monkeypatch.setattr("houses.routing._google_transit_commute", mock_google)
        monkeypatch.setattr("houses.routing._tfl_transit_commute", mock_tfl)

        result = await get_commute("GU21 7QF", "EC3A 7LP", has_car=False, max_walk_minutes=30)
        assert result.is_succeeded, f"Expected succeeded, got {result}"
        assert not tfl_called, "TfL was called even though Google had pricing"
        assert result.value_or_none().daily_cost_gbp == 5.0


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
            daily_cost_gbp=20.0,
        )
        with_bus = Commute(
            destination_label="",
            destination_postcode="SW1V 2QQ",
            duration_minutes=70,
            daily_cost_gbp=15.0,
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
            daily_cost_gbp=15.0,
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


# ── Google transit: bus rejection when allow_bus=False ────────────────
# Google Routes may return bus legs even when ``allowedTravelModes``
# excludes BUS (it's a preference, not a strict filter).  When
# ``allow_bus=False`` and the response still contains bus legs, the
# route must be rejected so the caller falls back to TfL park-and-ride.


def _fake_bus_step(seconds: int, line_name: str, dep: str, arr: str) -> dict:
    """Build a Google Routes step dict for a BUS transit leg."""
    return {
        "travelMode": "TRANSIT",
        "staticDuration": f"{seconds}s",
        "transitDetails": {
            "transitLine": {"vehicle": {"type": "BUS"}, "nameShort": line_name},
            "stopDetails": {
                "departureStop": {"name": dep, "location": {"latLng": {"latitude": 51.3, "longitude": -0.5}}},
                "arrivalStop": {"name": arr, "location": {"latLng": {"latitude": 51.3, "longitude": -0.5}}},
            },
        },
    }


_GOOGLE_ROUTES_WITH_BUS = {
    "routes": [
        {
            "duration": "3600s",
            "legs": [
                {
                    "steps": [
                        {"travelMode": "WALK", "staticDuration": "120s"},
                        _fake_bus_step(600, "91", "Randolph Close", "Woking Station"),
                        {
                            "travelMode": "TRANSIT",
                            "staticDuration": "2400s",
                            "transitDetails": {
                                "transitLine": {"vehicle": {"type": "RAIL"}, "nameShort": "SWR"},
                                "stopDetails": {
                                    "departureStop": {"name": "Woking"},
                                    "arrivalStop": {"name": "Waterloo"},
                                },
                            },
                        },
                        {"travelMode": "WALK", "staticDuration": "300s"},
                    ],
                }
            ],
        }
    ]
}


class TestGoogleTransitRejectsBusWhenDisabled:
    """_google_transit_commute must reject bus-inclusive routes when allow_bus=False."""

    @pytest.mark.asyncio
    async def test_rejects_bus_route_when_disabled(self, monkeypatch):
        """allow_bus=False + Google returns bus → return None so caller falls back to TfL."""
        from houses.routing import _google_transit_commute

        async def mock_post(*_, **__):
            return _GOOGLE_ROUTES_WITH_BUS

        monkeypatch.setattr("houses.routing._google_routes_post", mock_post)
        monkeypatch.setattr("houses.routing._bus_fare_for", lambda *_, **__: 3.0)

        result = await _google_transit_commute("GU21 2NA", "SW1V 2QQ", allow_bus=False)
        assert result is None, (
            f"Expected None (bus route rejected), got a commute with "
            f"{sum(1 for cg in (result or {}).cost_groups or [] for leg in cg.legs if leg.mode.name == 'BUS')} bus legs"
        )

    @pytest.mark.asyncio
    async def test_accepts_bus_route_when_enabled(self, monkeypatch):
        """allow_bus=True + Google returns bus → route is accepted."""
        from houses.routing import _google_transit_commute

        async def mock_post(*_, **__):
            return _GOOGLE_ROUTES_WITH_BUS

        monkeypatch.setattr("houses.routing._google_routes_post", mock_post)
        monkeypatch.setattr("houses.routing._bus_fare_for", lambda *_, **__: 3.0)

        result = await _google_transit_commute("GU21 2NA", "SW1V 2QQ", allow_bus=True)
        assert result is not None, "Expected a route with bus, got None"

        bus_legs = [leg for cg in result.cost_groups for leg in cg.legs if leg.mode.name == "BUS"]
        assert len(bus_legs) > 0, "Expected bus legs in the route"


# ── Park-and-ride creates parking CostGroup ─────────────────────────


class TestParkAndRideCostGroup:
    """_add_parking_cost must return a CostGroup with parking cost so
    ``Simon Parking Cost (£)`` (derived from ``non_rail_cost()``) shows
    the real parking fee, not bus fares."""

    @pytest.mark.asyncio
    async def test_returns_parking_cost_group(self, monkeypatch, tmp_path):
        """_add_parking_cost returns a parking CostGroup with cost, operator='ParkCo'."""
        from money import Money

        from houses.commute import LegMode
        from houses.transit_route import TransitRoute

        csv_path = tmp_path / "parking_rates.csv"
        csv_path.write_text("station_name,crs,daily_cost_gbp\nFleet,FLE,10.90\n")
        monkeypatch.setattr("houses.car_park._PARKING_RATES_PATH", csv_path)

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

        parking_cost, new_cost, parking_groups = await route._add_parking_cost(data, 30.0)

        assert parking_cost == 10.90, f"Expected 10.90, got {parking_cost}"
        assert new_cost == 40.90, f"Expected 40.90, got {new_cost}"
        assert len(parking_groups) == 1, "Expected one parking CostGroup"
        assert parking_groups[0].cost == Money("10.90", "GBP"), (
            f"Parking CostGroup should have cost=Money('10.90', 'GBP'), got {parking_groups[0].cost}"
        )
        assert parking_groups[0].legs[0].mode == LegMode.PARK, "Parking CostGroup should have LegMode.PARK"


# ── Google transit walk grouping ────────────────────────────────────────
# Google Routes may return consecutive walk segments.  _google_transit_commute
# must group them into a single walk leg with the correct total duration.


def _fake_google_steps(*step_specs: tuple[str, int]) -> list[dict]:
    """Build a list of Google Routes step dicts from (mode, seconds) pairs.

    Mode is ``"WALK"`` or ``"TRANSIT"``.  Transit steps get a default
    ``subway`` vehicle type so they parse correctly.
    """
    steps = []
    for mode, sec in step_specs:
        step: dict = {
            "travelMode": mode,
            "staticDuration": f"{sec}s",
        }
        if mode == "TRANSIT":
            step["transitDetails"] = {
                "transitLine": {"vehicle": {"type": "SUBWAY"}, "nameShort": "Victoria"},
                "stopDetails": {
                    "departureStop": {"name": "Start", "location": {"latLng": {"latitude": 51.5, "longitude": -0.1}}},
                    "arrivalStop": {"name": "End", "location": {"latLng": {"latitude": 51.5, "longitude": -0.1}}},
                },
            }
        steps.append(step)
    return steps


_GOOGLE_ROUTES_RESPONSE = {
    "routes": [
        {
            "duration": "900s",
            "legs": [
                {
                    "steps": _fake_google_steps(
                        ("WALK", 60),  # walk 1
                        ("WALK", 90),  # walk 2 — should merge with walk 1
                        ("TRANSIT", 300),  # tube
                        ("WALK", 30),  # walk 3
                        ("WALK", 45),  # walk 4 — should merge with walk 3
                        ("TRANSIT", 120),  # tube
                        ("WALK", 120),  # walk 5
                        ("WALK", 0),  # walk 6 — 0-second, should be skipped
                    ),
                }
            ],
        }
    ]
}


class TestGoogleTransitMissingWalk:
    """When Google omits the walk to the first transit stop, _google_transit_commute
    computes it via the walking API and prepends a walk CostGroup."""

    @pytest.mark.asyncio
    async def test_walk_to_ascot_not_2_minutes(self, monkeypatch):
        """Simon's commute from 174660728 (Ascot, SL5 → Pimlico) must include
        a walk to Ascot station of ~19 min (1.95 km).  The route summary
        must show 'walk to Ascot' with a duration >= 5 min, not ~2 min."""
        import json
        from pathlib import Path

        from houses.routing import _google_transit_commute

        # Google transit response that starts with TRANSIT at Ascot — no walk leg
        google_transit_response = json.loads(Path("data/api_cache/a9ae12e7b0fb8e500cd7280b710d3e90.json").read_text())

        async def mock_routes_post(body, field_mask, *, timeout=10.0):
            if body.get("travelMode") == "WALK":
                # _walk_to_station_minutes is calling for the walking duration.
                # Validate that the body uses the correct Google Routes API v2 format:
                # "destination": {"location": {"latLng": {...}}}
                # If the format is wrong (just {"latLng": {...}}), the real API
                # returns 400 INVALID_ARGUMENT.
                dest = body.get("destination", {})
                if "location" not in dest:
                    # Wrong format — simulate the API error
                    return None
                return {"routes": [{"duration": "1140s"}]}  # 19 min
            # Transit request
            return google_transit_response

        monkeypatch.setattr("houses.routing._google_routes_post", mock_routes_post)
        monkeypatch.setattr("houses.routing._bus_fare_for", lambda *_, **__: None)

        # Do NOT mock _walk_to_station_minutes — the real function calls
        # _google_routes_post (which is mocked above) and returns the correct
        # walking duration when the body format is right.

        result = await _google_transit_commute("SL5", "SW1V 2QQ", allow_bus=True)
        assert result is not None, "Expected a commute result"

        summary = result.summary()
        assert "walk to ascot" in summary.lower(), (
            f"Simon's commute summary does not mention walking to Ascot station. "
            f"Got: {summary}. Ascot station is 1.95 km from the property — "
            f"Simon must walk, not teleport."
        )
        # The walk to Ascot should be ~19 min for 1.95 km, not 2 min
        assert "(19m)" in summary, f"Walk to Ascot shows wrong duration. Expected ~19 min, got: {summary}"

    _GOOGLE_NO_WALK_RESPONSE = {
        "routes": [
            {
                "duration": "3600s",
                "legs": [
                    {
                        "steps": [
                            {
                                "travelMode": "TRANSIT",
                                "staticDuration": "3000s",
                                "transitDetails": {
                                    "transitLine": {"nameShort": "South Western Railway", "vehicle": {"type": "RAIL"}},
                                    "stopDetails": {
                                        "departureStop": {
                                            "name": "Ascot",
                                            "location": {"latLng": {"latitude": 51.4063, "longitude": -0.6762}},
                                        },
                                        "arrivalStop": {"name": "Vauxhall"},
                                    },
                                },
                            },
                            {
                                "travelMode": "WALK",
                                "staticDuration": "120s",
                            },
                        ],
                    }
                ],
            }
        ]
    }

    @pytest.mark.asyncio
    async def test_walk_to_ascot_gives_2_min_not_19(self, monkeypatch):
        """_walk_to_station_minutes with the ambiguous address returns 2 min,
        but with the actual property coordinates returns ~19 min."""
        from houses.routing import _walk_to_station_minutes

        # Mock the walking API: address-based → 2 min (buggy geocoding),
        # coordinate-based → 19 min (precise location)
        def mock_response(body):
            if body.get("travelMode") == "WALK":
                if "address" in body.get("origin", {}):
                    return {"routes": [{"duration": "120s"}]}  # 2 min
                return {"routes": [{"duration": "1140s"}]}  # 19 min
            return None

        async def mock_post(body, *_, **__):
            return mock_response(body)

        monkeypatch.setattr("houses.routing._google_routes_post", mock_post)

        # With the ACTUAL coordinates (51.414627, -0.70056 from the sheet):
        # uses precise location → ~19 min (correct)
        result_with_coords = await _walk_to_station_minutes(
            "Sunningdale, Ascot, SL5",
            51.4063,
            -0.6762,
            origin_latlng=(51.414627, -0.70056),
        )
        assert result_with_coords is not None
        assert result_with_coords >= 10, (
            f"_walk_to_station_minutes with actual coordinates returned "
            f"{result_with_coords} min. The correct walk for 1.95 km is "
            f"~19 min — check origin_latlng is being used."
        )

    @pytest.mark.asyncio
    async def test_adds_walk_when_first_step_is_transit(self, monkeypatch):
        """First step is TRANSIT → walk leg computed via _walk_to_station_minutes and prepended."""
        from houses.routing import _google_transit_commute

        async def mock_post(*_, **__):
            return self._GOOGLE_NO_WALK_RESPONSE

        async def mock_walk(*_, **__):
            return 24  # 24 min walk to Ascot station

        monkeypatch.setattr("houses.routing._google_routes_post", mock_post)
        monkeypatch.setattr("houses.routing._walk_to_station_minutes", mock_walk)
        monkeypatch.setattr("houses.routing._bus_fare_for", lambda *_, **__: None)

        result = await _google_transit_commute("SL5", "SW1V 2QQ", allow_bus=True)
        assert result is not None, "Expected a commute result"

        # First cost group should be a walk
        first = result.cost_groups[0]
        assert first.legs[0].mode.name == "WALK", f"Expected first leg to be WALK, got {first.legs[0].mode.name}"
        assert first.legs[0].duration_minutes == 24, f"Expected 24 min walk, got {first.legs[0].duration_minutes}"
        assert first.legs[0].end_station == "Ascot", f"Expected walk to Ascot, got {first.legs[0].end_station}"

    @pytest.mark.asyncio
    async def test_duration_includes_walk_time(self, monkeypatch):
        """Total duration_minutes includes the added walk time."""
        from houses.routing import _google_transit_commute

        async def mock_post(*_, **__):
            return self._GOOGLE_NO_WALK_RESPONSE

        async def mock_walk(*_, **__):
            return 24

        monkeypatch.setattr("houses.routing._google_routes_post", mock_post)
        monkeypatch.setattr("houses.routing._walk_to_station_minutes", mock_walk)
        monkeypatch.setattr("houses.routing._bus_fare_for", lambda *_, **__: None)

        result = await _google_transit_commute("SL5", "SW1V 2QQ", allow_bus=True)
        assert result is not None

        # Original duration was 3600s = 60 min. Adding 24 min walk → 84 min.
        assert result.duration_minutes == 84, f"Expected 84 min (60 + 24 walk), got {result.duration_minutes}"

    @pytest.mark.asyncio
    async def test_no_walk_added_when_first_step_is_walk(self, monkeypatch):
        """First step is already WALK → no walk leg is added."""
        from houses.routing import _google_transit_commute

        # Reuse the standard Google transit response (starts with walk)
        async def mock_post(*_, **__):
            from tests.unit.test_routing import _GOOGLE_ROUTES_RESPONSE

            return _GOOGLE_ROUTES_RESPONSE

        monkeypatch.setattr("houses.routing._google_routes_post", mock_post)
        monkeypatch.setattr("houses.routing._bus_fare_for", lambda *_, **__: None)

        result = await _google_transit_commute("SW1V 2QQ", "EC3A 7LP", allow_bus=True)
        assert result is not None

        # First cost group was already a walk from the Google response
        first = result.cost_groups[0]
        assert first.legs[0].mode.name == "WALK"


class TestGoogleTransitWalkGrouping:
    """_google_transit_commute merges consecutive walk segments."""

    @pytest.mark.asyncio
    async def test_merges_consecutive_walks(self, monkeypatch):
        """Consecutive WALK steps become one leg with combined duration."""
        from houses.routing import _google_transit_commute

        async def mock_post(*_, **__):
            return _GOOGLE_ROUTES_RESPONSE

        monkeypatch.setattr("houses.routing._google_routes_post", mock_post)
        monkeypatch.setattr("houses.routing._bus_fare_for", lambda *_, **__: None)

        result = await _google_transit_commute("SW1V 2QQ", "EC3A 7LP")
        assert result is not None, "Expected a commute result"

        # Extract walk legs by scanning cost groups
        walk_legs = []
        for g in result.cost_groups:
            for leg in g.legs:
                if leg.mode.name == "WALK":
                    walk_legs.append(leg)

        # We should have exactly 3 walk legs: 60+90s, 30+45s, 120+0s
        assert len(walk_legs) == 3, (
            f"Expected 3 walk legs (consecutive merged), got {len(walk_legs)}: "
            f"{[leg.duration_minutes for leg in walk_legs]}"
        )

        # Walk totals: 150s→2min, 75s→1min, 120s→2min (round halves to even)
        durations = sorted([leg.duration_minutes for leg in walk_legs])
        assert durations == [1, 2, 2], f"Expected walk durations [1, 2, 2] from merged seconds, got {durations}"

    @pytest.mark.asyncio
    async def test_no_consecutive_walk_modes(self, monkeypatch):
        """No two consecutive cost groups should both be WALK."""
        from houses.routing import _google_transit_commute

        async def mock_post(*_, **__):
            return _GOOGLE_ROUTES_RESPONSE

        monkeypatch.setattr("houses.routing._google_routes_post", mock_post)
        monkeypatch.setattr("houses.routing._bus_fare_for", lambda *_, **__: None)

        result = await _google_transit_commute("SW1V 2QQ", "EC3A 7LP")
        assert result is not None, "Expected a commute result"

        # Check that no two consecutive cost groups are both walk-only
        modes = []
        for g in result.cost_groups:
            modes.append(g.legs[0].mode.name)

        for i in range(len(modes) - 1):
            if modes[i] == "WALK" and modes[i + 1] == "WALK":
                pytest.fail(f"Found consecutive WALK cost groups at indices {i} and {i + 1}: {modes}")


class TestCostGroupBuilder:
    """_CostGroupBuilder — walk merging, short-walk threshold, bus grouping."""

    def test_short_walk_shows_one_minute(self):
        """A 22-second walk → "walk (1m)" — not dropped."""
        from houses.routing import _CostGroupBuilder

        b = _CostGroupBuilder()
        b.add_walk(22)
        b.flush_walk()
        assert len(b.cost_groups) == 1
        assert b.cost_groups[0].legs[0].mode == LegMode.WALK
        assert b.cost_groups[0].legs[0].duration_minutes == 1

    def test_tiny_walk_dropped(self):
        """A 5-second walk is below the 10s threshold → no walk leg."""
        from houses.routing import _CostGroupBuilder

        b = _CostGroupBuilder()
        b.add_walk(5)
        b.flush_walk()
        assert len(b.cost_groups) == 0

    def test_consecutive_walks_merged(self):
        """Two short walks (8s + 8s = 16s) exceed the threshold → merged."""
        from houses.routing import _CostGroupBuilder

        b = _CostGroupBuilder()
        b.add_walk(8)
        b.add_walk(8)
        b.flush_walk()
        assert len(b.cost_groups) == 1
        assert b.cost_groups[0].legs[0].duration_minutes == 1

    def test_bus_and_walk_grouping(self):
        """Flushing bus doesn't flush walk, and vice versa."""
        from houses.routing import _CostGroupBuilder

        b = _CostGroupBuilder()
        b.add_walk(30)
        b.flush_bus()  # no bus legs to flush — no-op
        b.flush_walk()
        assert len(b.cost_groups) == 1, "bus flush should not affect walk"

        b2 = _CostGroupBuilder()
        b2.add_bus_leg(JourneyLeg(mode=LegMode.BUS, duration_minutes=5), cost=2.0)
        b2.add_walk(30)
        # add_walk calls flush_bus, so the bus leg should be flushed
        assert len(b2.cost_groups) == 1, "walk should flush bus"
        assert b2.cost_groups[0].legs[0].mode == LegMode.BUS

    def test_empty_no_crash(self):
        """No steps added — no cost groups, no crash."""
        from houses.routing import _CostGroupBuilder

        b = _CostGroupBuilder()
        b.flush_walk()
        b.flush_bus()
        assert len(b.cost_groups) == 0


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
                daily_cost_gbp=0.0,
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
