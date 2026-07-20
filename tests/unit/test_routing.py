"""Tests for houses/routing.py — get_commute(), _google_route_commute, etc."""

from __future__ import annotations

import pytest
from money import Money
from pint import Quantity

from houses.commute import CostGroup, JourneyLeg, LegMode
from houses.model.domain import Commute, Person, PlaceOfInterest

# ── Fail-fast when API keys are missing ─────────────────────────────────


class TestWalkCommuteFailsFast:
    """_google_route_commute must raise ValueError when Google API key is missing."""

    def test_raises_without_api_key(self):
        import asyncio

        from houses.config import settings
        from houses.routing import _google_route_commute

        original = settings.google_maps_api_key
        try:
            settings.google_maps_api_key = ""
            with pytest.raises(ValueError, match="Google Maps API key not configured"):
                asyncio.run(_google_route_commute("SW1V 2QQ", "EC3A 7LP", "WALK"))
        finally:
            settings.google_maps_api_key = original

    def test_raise_with_body_includes_response_text(self):
        """_raise_with_body must include the response body in the error
        message so the reason for a 400 (e.g. "LatLng cannot be specified
        as an Address Waypoint") appears in the traceback."""
        import httpx
        from houses.routing import _raise_with_body

        resp = httpx.Response(
            status_code=400,
            text='{"error":"bad request"}',
            request=httpx.Request("POST", "http://example.com/api"),
        )
        with pytest.raises(httpx.HTTPStatusError) as exc:
            _raise_with_body(resp)
        assert '{"error":"bad request"}' in str(exc.value), (
            f"Response body should be appended to error message. "
            f"Got: {str(exc.value)}"
        )



@pytest.mark.asyncio
async def test_find_nearest_handles_coordinate_string(monkeypatch):
    """find_nearest must accept a 'lat,lon' coordinate string and use it
    directly instead of trying to geocode it."""
    from houses.geo import GeoPoint
    from houses.school_gender import SchoolGender
    from houses.schools import School, find_nearest

    # Fake school at a known location
    fake_school = School(
        urn="1",
        name="Test Primary",
        phase="Primary",
        gender=SchoolGender.MIXED,
        type_of_establishment="community school",
        postcode="SW1V 2QQ",
        website="",
        ofsted_rating="Good",
        inspection_year="2022",
        coords=GeoPoint(lat=51.5, lon=-0.13),
        statutory_low_age=4,
        statutory_high_age=11,
    )
    geocode_called = False

    async def fake_geocode(_input):
        nonlocal geocode_called
        geocode_called = True
        from dag.attempt import Attempt

        return Attempt.pending()  # geocode can't parse coordinate strings

    monkeypatch.setattr("houses.schools.geocode", fake_geocode)
    monkeypatch.setattr("houses.schools._geocode_address", fake_geocode)
    monkeypatch.setattr("houses.schools._load_schools", lambda: [fake_school])

    result = await find_nearest("51.5,-0.13", child_age=4, acceptable=(SchoolGender.MIXED,))

    assert result is not None, "find_nearest should find a school from coordinate input"
    assert result.name == "Test Primary"
    assert not geocode_called, "find_nearest should NOT call geocode when given coordinates"


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
    person=Person(name="", has_car=False),
    label="",
    destination=PlaceOfInterest(label="", postcode=""),
    duration=Quantity(60, "minute"),
    daily_cost=Money("0.0", "GBP"),
    mode="walk",
)
_WALK_20 = Commute(
    person=Person(name="", has_car=False),
    label="",
    destination=PlaceOfInterest(label="", postcode=""),
    duration=Quantity(20, "minute"),
    daily_cost=Money("0.0", "GBP"),
    mode="walk",
)
_TRANSIT_30 = Commute(
    person=Person(name="", has_car=False),
    label="",
    destination=PlaceOfInterest(label="", postcode=""),
    duration=Quantity(30, "minute"),
    daily_cost=Money("8.0", "GBP"),
    mode="transit",
)
_DRIVE_25 = Commute(
    person=Person(name="", has_car=False),
    label="",
    destination=PlaceOfInterest(label="", postcode=""),
    duration=Quantity(25, "minute"),
    daily_cost=Money("5.0", "GBP"),
    mode="drive",
)

# Tiebreak fixture — route with cost, used by test_returns_cost_when_tfl_has_cost
_SLOWER_HAS_COST = Commute(
    person=Person(name="", has_car=False),
    label="",
    destination=PlaceOfInterest(label="", postcode=""),
    duration=Quantity(25, "minute"),
    daily_cost=Money("5.0", "GBP"),
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

        monkeypatch.setattr("houses.routing._google_route_commute", mock_walk)
        monkeypatch.setattr("houses.routing._tfl_transit_commute", mock_transit)

        result = await get_commute("GU21 7QF", "SW1V 2QQ", has_car=False, max_walk_minutes=30)
        assert result.succeeded, f"Expected succeeded, got {result}"
        assert result.value_or_none().duration.magnitude == 20

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

        monkeypatch.setattr("houses.routing._google_route_commute", mock_walk)
        monkeypatch.setattr("houses.routing._tfl_transit_commute", mock_transit)

        result = await get_commute("GU21 7QF", "SW1V 2QQ", has_car=False, max_walk_minutes=30)
        assert result.succeeded, f"Expected succeeded, got {result}"
        assert result.value_or_none().duration.magnitude == 30  # transit, not walking

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

        monkeypatch.setattr("houses.routing._google_route_commute", mock_walk)
        monkeypatch.setattr("houses.routing._tfl_transit_commute", mock_transit)

        monkeypatch.setattr("houses.routing._google_route_commute", mock_drive)
        monkeypatch.setattr("houses.routing._in_congestion_zone", mock_cz)

        result = await get_commute("GU21 7QF", "RG12 8YA", has_car=True, max_walk_minutes=15)
        assert result.succeeded, f"Expected succeeded, got {result}"
        assert result.value_or_none().duration.magnitude == 25  # driving

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

        monkeypatch.setattr("houses.routing._google_route_commute", mock_walk)
        monkeypatch.setattr("houses.routing._tfl_transit_commute", mock_transit)

        monkeypatch.setattr("houses.routing._google_route_commute", mock_drive)
        monkeypatch.setattr("houses.routing._in_congestion_zone", mock_cz)

        result = await get_commute("GU21 7QF", "RG12 8YA", has_car=True, max_walk_minutes=15)
        assert result.succeeded, f"Expected succeeded, got {result}"
        assert result.value_or_none().duration.magnitude == 25  # driving is faster than transit

    @pytest.mark.asyncio
    async def test_skips_driving_for_congestion_zone(self, monkeypatch):
        """Central London destinations should never try driving."""
        from houses.routing import get_commute

        async def mock_transit(*_, **__):
            return _TRANSIT_30

        async def mock_routes(origin, dest, mode, max_walk_minutes=None):
            if mode == "WALK":
                return None
            if mode == "DRIVE":
                return _DRIVE_25
            return None

        def mock_cz(_):
            return True

        monkeypatch.setattr("houses.routing._google_route_commute", mock_routes)
        monkeypatch.setattr("houses.routing._tfl_transit_commute", mock_transit)
        monkeypatch.setattr("houses.routing._in_congestion_zone", mock_cz)

        result = await get_commute("GU21 7QF", "SW1V 2QQ", has_car=True, max_walk_minutes=15)
        assert result.succeeded, f"Expected succeeded, got {result}"
        assert result.value_or_none().duration.magnitude == 30  # transit, not driving

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

        monkeypatch.setattr("houses.routing._google_route_commute", mock_walk)
        monkeypatch.setattr("houses.routing._tfl_transit_commute", mock_transit)
    @pytest.mark.asyncio
    async def test_find_bus_alternative_uses_latlng_for_coord_origin(self, monkeypatch):
        """_find_bus_alternative must call _address_waypoint to convert
        coordinate strings to latLng waypoints, not hardcode {"address": ...}.
        """
        import json
        from houses.routing import _find_bus_alternative
        from houses.config import settings

        bodies: list[dict] = []

        async def capture_google_routes_post(body, field_mask, **kw):
            bodies.append(body)
            return None

        monkeypatch.setattr("houses.routing._google_routes_post", capture_google_routes_post)

        original_key = settings.google_maps_api_key
        try:
            settings.google_maps_api_key = "test-key"
            await _find_bus_alternative("51.6,-1.25", "EC3A 7LP")
        finally:
            settings.google_maps_api_key = original_key

        assert len(bodies) > 0, "_find_bus_alternative should call Google Routes"
        body = bodies[0]
        origin_wp = body.get("origin", {})
        assert "location" in origin_wp, (
            f"Origin waypoint for coord string must use 'location' (latLng), "
            f"got {json.dumps(origin_wp, indent=2)}. "
            f"Sending {{'address': 'lat,lon'}} causes Google Routes to return 400."
        )
        assert "latLng" in origin_wp.get("location", {}), (
            f"Expected latLng in origin waypoint, "
            f"got {json.dumps(origin_wp, indent=2)}"
        )


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

        monkeypatch.setattr("houses.routing._google_route_commute", mock_walk)
        monkeypatch.setattr("houses.routing._tfl_transit_commute", mock_tfl)

        result = await get_commute("GU21 7QF", "EC3A 7LP", has_car=False, max_walk_minutes=30)
        assert result.succeeded, f"Expected succeeded, got {result}"
        best = result.value_or_none()
        assert best.daily_cost == Money("5.0", "GBP"), "Should return the route with a real cost"


# ── TfL: no bus when has_car=True ────────────────────────────────────


class TestTflNoBusWhenHasCar:
    """_tfl_transit_commute skips with_bus when no_bus succeeds."""

    @pytest.mark.asyncio
    async def test_skips_with_bus_when_no_bus_succeeds(self, monkeypatch):
        """has_car=True + no_bus succeeds → with_bus is not compared."""
        from dag.attempt import Attempt

        no_bus = Commute(
            person=Person(name="", has_car=True),
            label="",
            destination=PlaceOfInterest(label="", postcode="SW1V 2QQ"),
            duration=Quantity(90, "minute"),
            daily_cost=Money("20.0", "GBP"),
        )
        with_bus = Commute(
            person=Person(name="", has_car=True),
            label="",
            destination=PlaceOfInterest(label="", postcode="SW1V 2QQ"),
            duration=Quantity(70, "minute"),
            daily_cost=Money("15.0", "GBP"),
        )

        call_count = 0

        async def mock_plan(self):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return Attempt.succeeded(no_bus)
            return Attempt.succeeded(with_bus)

        from houses.routing import _tfl_transit_commute

        monkeypatch.setattr("houses.transit_route.TransitRoute.plan", mock_plan)

        result = await _tfl_transit_commute("GU21 2NA", "EC3A 7LP", has_car=True)
        assert result is not None
        assert result.duration.magnitude == 90, (
            f"Expected no_bus (90 min), got {result.duration.magnitude} — with_bus was compared when no_bus succeeded"
        )

    @pytest.mark.asyncio
    async def test_uses_with_bus_when_no_bus_fails(self, monkeypatch):
        """has_car=True + no_bus fails → with_bus is used as last resort."""
        from dag.attempt import Attempt

        no_bus = Attempt.impossible("no route found")
        with_bus = Commute(
            person=Person(name="", has_car=True),
            label="",
            destination=PlaceOfInterest(label="", postcode="SW1V 2QQ"),
            duration=Quantity(70, "minute"),
            daily_cost=Money("15.0", "GBP"),
        )

        call_count = 0

        async def mock_plan(self):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return no_bus
            return Attempt.succeeded(with_bus)

        from houses.routing import _tfl_transit_commute

        monkeypatch.setattr("houses.transit_route.TransitRoute.plan", mock_plan)

        result = await _tfl_transit_commute("GU21 2NA", "EC3A 7LP", has_car=True)
        assert result is not None
        assert result.duration.magnitude == 70, f"Expected with_bus (70 min), got {result.duration.magnitude}"


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
        from houses.school import School
        from houses.school_gender import SchoolGender
        from houses.schools import compute_school_commute

        captured = {}

        async def mock_get_commute(origin, dest, *, has_car, max_walk_minutes):
            captured.update(origin=origin, dest=dest, has_car=has_car, max_walk_minutes=max_walk_minutes)
            from dag.attempt import Attempt

            commute = Commute(
                person=Person(name="", has_car=False),
                label="",
                destination=PlaceOfInterest(label="", postcode=dest),
                duration=Quantity(10, "minute"),
                daily_cost=Money("0.0", "GBP"),
            )
            return Attempt.succeeded(commute)

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
        assert result.duration.magnitude == 10
        assert captured["has_car"] is False
        assert captured["max_walk_minutes"] == 20
        assert captured["origin"] == "SL6 1AA"
        assert captured["dest"] == "SL6 1AA"


# ── _replace_walk_with_bus ──────────────────────────────────────────────


def _tfl_complete(duration=90, cost="12.50", walk=46) -> Commute:
    """A TfL commute with walk + train + tube legs and full cost."""
    return Commute(
        person=Person(name="", has_car=False),
        label="L",
        destination=PlaceOfInterest(label="L", postcode="EC3A 7LP"),
        duration=Quantity(duration, "minute"),
        daily_cost=Money(cost, "GBP"),
        details=(
            CostGroup(legs=(JourneyLeg(mode=LegMode.WALK, duration_minutes=walk),)),
            CostGroup(
                legs=(JourneyLeg(mode=LegMode.TRAIN, duration_minutes=42),),
            ),
            CostGroup(
                legs=(JourneyLeg(mode=LegMode.TUBE, duration_minutes=4),),
            ),
        ),
    )


def _bus_route() -> Commute:
    """A bus route that saves 8 min of walking for £3.80."""
    return Commute(
        person=Person(name="", has_car=False),
        label="L (Bus)",
        destination=PlaceOfInterest(label="L (Bus)", postcode="EC3A 7LP"),
        duration=Quantity(55, "minute"),
        daily_cost=Money("3.80", "GBP"),
        mode="transit",
        details=(
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
    assert result.daily_cost == Money("12.50", "GBP")


@pytest.mark.asyncio
async def test_replace_walk_with_bus_no_bus():
    """When no bus is available, the TfL commute is returned unchanged."""
    from houses.routing import _replace_walk_with_bus

    original = _tfl_complete(walk=46)
    result = await _replace_walk_with_bus(original, "GU22 8RU", "EC3A 7LP", 46, _bus_alternative=None)
    assert result is original


@pytest.mark.asyncio
async def test_replace_walk_with_bus_replaces_walk():
    """When the bus is viable, walking time is replaced and bus cost added."""
    from houses.routing import _replace_walk_with_bus

    original = _tfl_complete(duration=90, cost="12.50", walk=46)
    result = await _replace_walk_with_bus(original, "GU22 8RU", "EC3A 7LP", 46, _bus_alternative=_bus_route())
    # Duration: 90 - 46 + min(15, 46-10=36) = 90 - 46 + 15 = 59
    assert result.duration.magnitude == 59
    # Cost: TfL £12.50 + bus £3.80 = £16.30
    assert result.daily_cost == Money("16.30", "GBP")


@pytest.mark.asyncio
async def test_replace_walk_with_bus_short_walk_no_replace():
    """When walk is under the penalty threshold, no replacement is tried even with a bus."""
    from houses.routing import _replace_walk_with_bus

    original = _tfl_complete(duration=90, cost="12.50", walk=9)
    result = await _replace_walk_with_bus(original, "GU22 8RU", "EC3A 7LP", 9, _bus_alternative=_bus_route())
    assert result is original


class TestAddressWaypoint:
    """_address_waypoint must handle postcodes, GeoPoints, and coordinate strings."""

    def test_postcode_returns_address_waypoint(self):
        from houses.routing import _address_waypoint

        result = _address_waypoint("SW1V 2QQ")
        assert result == {"address": "SW1V 2QQ"}

    def test_geopoint_returns_location_waypoint(self):
        from houses.geo import GeoPoint
        from houses.routing import _address_waypoint

        gp = GeoPoint(lat=51.5, lon=-0.13)
        result = _address_waypoint(gp)
        assert result == {"location": {"latLng": {"latitude": 51.5, "longitude": -0.13}}}

    def test_coordinate_string_returns_location_waypoint(self):
        """'lat,lon' strings must use location format, not address."""
        from houses.routing import _address_waypoint

        result = _address_waypoint("51.5,-0.13")
        assert result == {"location": {"latLng": {"latitude": 51.5, "longitude": -0.13}}}

    def test_invalid_coordinate_string_falls_back_to_address(self):
        from houses.routing import _address_waypoint

        result = _address_waypoint("not-a-coordinate")
        assert result == {"address": "not-a-coordinate"}
