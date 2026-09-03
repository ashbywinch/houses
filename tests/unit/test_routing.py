"""Tests for houses/commute_router.py (was houses/routing.py) — get_commute(), _google_route_commute, etc."""

from __future__ import annotations

import pytest
from money import Money
from pint import Quantity

from dag.attempt import Attempt
from houses.commute import LegMode
from houses.model.domain import Commute, Person, PlaceOfInterest

# ── Fail-fast when API keys are missing ─────────────────────────────────


class TestWalkCommuteFailsFast:
    """_google_routes_post must raise ValueError when Google API key is missing."""

    def test_raises_without_api_key(self):
        """_google_routes_post must raise ValueError when Google API key is missing."""
        import asyncio

        from houses.commute_router import GoogleRoutesClient
        from houses.settings import settings

        client = GoogleRoutesClient()
        original = settings.google_maps_api_key
        try:
            settings.google_maps_api_key = ""
            with pytest.raises(ValueError, match="Google Maps API key not configured"):
                asyncio.run(client.post({}, "test"))
        finally:
            settings.google_maps_api_key = original

    def test_raise_with_body_includes_response_text(self):
        """_raise_with_body must include the response body in the error
        message so the reason for a 400 (e.g. "LatLng cannot be specified
        as an Address Waypoint") appears in the traceback."""
        import httpx

        from houses.commute_router import CommuteRouter

        resp = httpx.Response(
            status_code=400,
            text='{"error":"bad request"}',
            request=httpx.Request("POST", "http://example.com/api"),
        )
        with pytest.raises(httpx.HTTPStatusError) as exc:
            CommuteRouter._raise_with_body(resp)
        assert '{"error":"bad request"}' in str(exc.value), (
            f"Response body should be appended to error message. Got: {str(exc.value)}"
        )

    @pytest.mark.asyncio
    async def test_transit_route_daily_cost_is_never_none(self):
        """TflClient._process_data() must return daily_cost as Money,
        never None, even when the TfL response has no fare data.
        Used to crash _replace_walk_with_bus with
        'decimal.Decimal + NoneType'."""
        from money import Money

        from houses.tfl_client import TflClient

        route = TflClient("51.3,-0.58", "EC3A 7LP", "Aldgate")

        # Empty TfL response — no journeys, no fare data.
        # raw_cost will be None → daily_cost_gbp would be None
        # without the guard in _process_data.
        attempt = await route._process_data({"journeys": []})

        # The guard catches this: daily_cost_gbp = Money("0", "GBP")
        if attempt.succeeded:
            val = attempt.value_or_none()
            assert val is not None, f"expected a Commute value, got {val!r}"
            assert isinstance(val.daily_cost, Money), (
                f"daily_cost must be Money, got {type(val.daily_cost).__name__}. "
                f"The guard in _process_data should set it to Money('0', 'GBP') "
                f"when raw_cost is None."
            )
        else:
            # No journeys → impossible is also fine
            pass


@pytest.mark.asyncio
async def test_find_nearest_handles_coordinate_string():
    """find_nearest must accept a 'lat,lon' coordinate string and use it
    directly instead of trying to geocode it."""
    from houses.geopoint import GeoPoint
    from houses.school_gender import SchoolGender
    from houses.schools import School, SchoolLookupOptions, find_nearest

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

    result = await find_nearest(
        "51.5,-0.13",
        child_age=4,
        options=SchoolLookupOptions(
            acceptable=(SchoolGender.MIXED,),
            geocode_fn=fake_geocode,
            geocode_address_fn=fake_geocode,
            load_schools_fn=lambda: [fake_school],
        ),
    )

    assert result is not None, "find_nearest should find a school from coordinate input"
    school = result.value_or_none()
    assert school is not None
    assert school.name == "Test Primary"
    assert not geocode_called, "find_nearest should NOT call geocode when given coordinates"


# ── Congestion zone ─────────────────────────────────────────────────────


class TestCongestionZone:
    """in_congestion_zone must correctly identify central London postcodes."""

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
        from houses.commute_router import CommuteRouter

        assert CommuteRouter.in_congestion_zone(postcode) == expected


# ── get_commute decision logic (backends mocked) ────────────────────────

_WALK_60 = Commute(
    person=Person(name="", has_car=False),
    label="",
    destination=PlaceOfInterest(label="", address=""),
    duration=Quantity(60, "minute"),
    daily_cost=Money("0.0", "GBP"),
    mode="walk",
)
_WALK_20 = Commute(
    person=Person(name="", has_car=False),
    label="",
    destination=PlaceOfInterest(label="", address=""),
    duration=Quantity(20, "minute"),
    daily_cost=Money("0.0", "GBP"),
    mode="walk",
)
_TRANSIT_30 = Commute(
    person=Person(name="", has_car=False),
    label="",
    destination=PlaceOfInterest(label="", address=""),
    duration=Quantity(30, "minute"),
    daily_cost=Money("8.0", "GBP"),
    mode="transit",
)
_DRIVE_25 = Commute(
    person=Person(name="", has_car=False),
    label="",
    destination=PlaceOfInterest(label="", address=""),
    duration=Quantity(25, "minute"),
    daily_cost=Money("5.0", "GBP"),
    mode="drive",
)

# Tiebreak fixture — route with cost, used by test_returns_cost_when_tfl_has_cost
_SLOWER_HAS_COST = Attempt.succeeded(
    Commute(
        person=Person(name="", has_car=False),
        label="",
        destination=PlaceOfInterest(label="", address=""),
        duration=Quantity(25, "minute"),
        daily_cost=Money("5.0", "GBP"),
    )
)


_PIMLICO = "1 Drummond Gate, Pimlico, London SW1V 2QQ"
_BRACKNELL = "Waite House, Doncastle Road, Bracknell, Berkshire RG12 8YA"
_ALDGATE = "Eastgate House, 40 Dukes Place, Aldgate, London EC3A 7LP"


class TestGetCommuteChoice:
    """get_commute picks the best option among walking, transit, driving."""

    @pytest.mark.asyncio
    async def test_walking_wins_when_fastest(self):
        """Walking within max_walk_minutes should be returned immediately."""

        from houses.commute_router import CommuteRouter

        async def mock_walk(*_):
            return Attempt.succeeded(_WALK_20)

        async def mock_transit(*_, **__):
            return Attempt.succeeded(_TRANSIT_30)

        router = CommuteRouter(google_route_fn=mock_walk, tfl_transit_fn=mock_transit)
        result = await router.get_commute("GU21 7QF", _PIMLICO, has_car=False, max_walk_minutes=30)
        assert result.succeeded, f"Expected succeeded, got {result}"
        commute = result.value_or_none()
        assert commute is not None
        assert commute.duration.magnitude == 20

    @pytest.mark.asyncio
    async def test_walking_skipped_when_too_slow(self):
        from houses.commute_router import CommuteRouter

        async def mock_walk(*_):
            return Attempt.succeeded(_WALK_60)

        async def mock_transit(*_, **__):
            return Attempt.succeeded(_TRANSIT_30)

        router = CommuteRouter(google_route_fn=mock_walk, tfl_transit_fn=mock_transit)
        result = await router.get_commute("GU21 7QF", _PIMLICO, has_car=False, max_walk_minutes=30)
        assert result.succeeded, f"Expected succeeded, got {result}"
        commute = result.value_or_none()
        assert commute is not None
        assert commute.duration.magnitude == 30  # transit, not walking

    @pytest.mark.asyncio
    async def test_driving_considered_when_has_car(self):
        """With has_car=True and no congestion zone, driving should be an option."""

        async def mock_routes(origin, dest, mode, max_walk_minutes=None):
            if mode == "WALK":
                return Attempt.succeeded(_WALK_60)
            if mode == "DRIVE":
                return Attempt.succeeded(_DRIVE_25)
            return Attempt.impossible("none")

        async def mock_transit(*_, **__):
            return Attempt.impossible("none")  # no transit available

        from houses.commute_router import CommuteRouter

        router = CommuteRouter(
            google_route_fn=mock_routes,
            tfl_transit_fn=mock_transit,
            congestion_fn=lambda _: False,
        )
        result = await router.get_commute("GU21 7QF", _BRACKNELL, has_car=True, max_walk_minutes=15)
        assert result.succeeded, f"Expected succeeded, got {result}"
        commute = result.value_or_none()
        assert commute is not None
        assert commute.duration.magnitude == 25  # driving

    @pytest.mark.asyncio
    async def test_prefers_faster_of_transit_and_drive(self):
        """With both transit and driving available, picks the faster one."""

        async def mock_routes(origin, dest, mode, max_walk_minutes=None):
            if mode == "WALK":
                return Attempt.succeeded(_WALK_60)
            if mode == "DRIVE":
                return Attempt.succeeded(_DRIVE_25)
            return Attempt.impossible("none")

        async def mock_transit(*_, **__):
            return Attempt.succeeded(_TRANSIT_30)

        from houses.commute_router import CommuteRouter

        router = CommuteRouter(
            google_route_fn=mock_routes,
            tfl_transit_fn=mock_transit,
            congestion_fn=lambda _: False,
        )
        result = await router.get_commute("GU21 7QF", _BRACKNELL, has_car=True, max_walk_minutes=15)
        assert result.succeeded, f"Expected succeeded, got {result}"
        commute = result.value_or_none()
        assert commute is not None
        assert commute.duration.magnitude == 25  # driving is faster than transit

    @pytest.mark.asyncio
    async def test_skips_driving_for_congestion_zone(self):
        """Central London destinations should never try driving."""
        from houses.commute_router import CommuteRouter

        async def mock_transit(*_, **__):
            return Attempt.succeeded(_TRANSIT_30)

        async def mock_routes(origin, dest, mode, max_walk_minutes=None):
            if mode == "WALK":
                return Attempt.impossible("no walk")
            if mode == "DRIVE":
                return Attempt.succeeded(_DRIVE_25)
            return Attempt.impossible("none")

        router = CommuteRouter(
            google_route_fn=mock_routes,
            tfl_transit_fn=mock_transit,
            congestion_fn=lambda _: True,
        )
        result = await router.get_commute("GU21 7QF", _PIMLICO, has_car=True, max_walk_minutes=15)
        assert result.succeeded, f"Expected succeeded, got {result}"
        commute = result.value_or_none()
        assert commute is not None
        assert commute.duration.magnitude == 30  # transit, not driving

    @pytest.mark.asyncio
    async def test_returns_impossible_when_no_route(self):
        """When all backends return None, get_commute returns Attempt.impossible."""
        from houses.commute_router import CommuteRouter

        async def mock_routes(*_, **__):
            return Attempt.impossible("none")

        router = CommuteRouter(
            google_route_fn=mock_routes,
            tfl_transit_fn=mock_routes,
            congestion_fn=lambda _: False,
        )
        result = await router.get_commute("GU21 7QF", _BRACKNELL, has_car=True, max_walk_minutes=15)
        assert result.impossible, f"Expected impossible, got {result}"

    # ── Tiebreak: priced vs non-priced routes ─────────────────────────
    # Requirement: "Have an accurate price for the whole journey" (#1).
    # When Google Routes returns a faster route without cost data and TfL
    # has a slightly slower route with a real cost, prefer TfL.  The NR
    # fare fallback can only approximate a rail fare — a real TfL cost
    # is more accurate.

    @pytest.mark.asyncio
    async def test_returns_cost_when_tfl_has_cost(self):
        """TfL returns a route with cost → it's selected."""

        async def mock_walk(*_):
            return Attempt.succeeded(_WALK_60)

        async def mock_tfl(*_, **__):
            return _SLOWER_HAS_COST  # 25 min, cost=5.0

        from houses.commute_router import CommuteRouter

        router = CommuteRouter(google_route_fn=mock_walk, tfl_transit_fn=mock_tfl)
        result = await router.get_commute("GU21 7QF", _ALDGATE, has_car=False, max_walk_minutes=30)
        assert result.succeeded, f"Expected succeeded, got {result}"
        best = result.value_or_none()
        assert best is not None
        assert best.daily_cost == Money("5.0", "GBP"), "Should return the route with a real cost"


# ── TfL: no bus when has_car=True ────────────────────────────────────


class TestTflNoBusWhenHasCar:
    """_tfl_transit_commute skips with_bus when no_bus succeeds."""

    @pytest.mark.asyncio
    async def test_skips_with_bus_when_no_bus_succeeds(self):
        """has_car=True + no_bus succeeds → with_bus is not compared."""
        from dag.attempt import Attempt
        from houses.commute_router import CommuteRouter
        from houses.services_provider import _request_services as _sp
        from tests.helpers import make_services

        no_bus = Commute(
            person=Person(name="", has_car=True),
            label="",
            destination=PlaceOfInterest(label="", address="SW1V 2QQ"),
            duration=Quantity(90, "minute"),
            daily_cost=Money("20.0", "GBP"),
        )
        with_bus = Commute(
            person=Person(name="", has_car=True),
            label="",
            destination=PlaceOfInterest(label="", address="SW1V 2QQ"),
            duration=Quantity(70, "minute"),
            daily_cost=Money("15.0", "GBP"),
        )

        class _FakeClient:
            """Canned TfL plan — injected via the services client factory.

            ``_tfl_transit_commute`` builds one client per plan() call, so the
            call sequence lives on the class, not the instance.
            """

            calls = 0

            def __init__(self, *args, **kwargs):
                self._plan_override = None
                self._no_route_detail = ""

            async def plan(self):
                type(self).calls += 1
                if type(self).calls == 1:
                    return Attempt.succeeded(no_bus)
                return Attempt.succeeded(with_bus)

        svc = make_services(tfl_client_factory=_FakeClient)
        token = _sp.set(svc)
        try:
            router = CommuteRouter()
            result = await router._tfl_transit_commute("GU21 2NA", "EC3A 7LP", has_car=True)
        finally:
            _sp.reset(token)
        assert result.succeeded, f"_tfl_transit_commute should succeed, got {result}"
        commute = result.value_or_none()
        assert commute is not None
        assert commute.duration.magnitude == 90, (
            f"Expected no_bus (90 min), got {commute.duration.magnitude}"
            " — with_bus was compared when no_bus succeeded"
        )

    @pytest.mark.asyncio
    async def test_uses_with_bus_when_no_bus_fails(self):
        """has_car=True + no_bus fails → with_bus is used as last resort."""
        from dag.attempt import Attempt
        from houses.commute_router import CommuteRouter
        from houses.services_provider import _request_services as _sp
        from tests.helpers import make_services

        no_bus = Attempt.impossible("no route found")
        with_bus = Commute(
            person=Person(name="", has_car=True),
            label="",
            destination=PlaceOfInterest(label="", address="SW1V 2QQ"),
            duration=Quantity(70, "minute"),
            daily_cost=Money("15.0", "GBP"),
        )

        class _FakeClient:
            """Canned TfL plan — injected via the services client factory.

            ``_tfl_transit_commute`` builds one client per plan() call, so the
            call sequence lives on the class, not the instance.
            """

            calls = 0

            def __init__(self, *args, **kwargs):
                self._plan_override = None
                self._no_route_detail = ""

            async def plan(self):
                type(self).calls += 1
                if type(self).calls == 1:
                    return no_bus
                return Attempt.succeeded(with_bus)

        svc = make_services(tfl_client_factory=_FakeClient)
        token = _sp.set(svc)
        try:
            router = CommuteRouter()
            result = await router._tfl_transit_commute("GU21 2NA", "EC3A 7LP", has_car=True)
        finally:
            _sp.reset(token)
        commute = result.value_or_none()
        assert commute is not None
        assert commute.duration.magnitude == 70, (
            f"Expected with_bus (70 min), got {commute.duration.magnitude}"
        )


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
        from houses.tfl_client import TflClient, TflRouteOptions

        registry = CarParkRegistry.from_car_parks(
            car_parks=[CarPark(name="Fleet", daily_cost=Money("10.90", "GBP"))],
            station_map={"fleet rail station": "Fleet"},
        )

        route = TflClient("SL6", "SW1V 2QQ", "test", options=TflRouteOptions(park_and_ride=True))
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

        result = await route._add_parking_cost(data, Money("30", "GBP"), _registry=registry)
        parking_cost, new_cost, parking_groups = result.parking_cost, result.new_cost, result.cost_groups
        assert parking_cost == Money("10.90", "GBP"), f"Expected 10.90, got {parking_cost}"
        assert new_cost == Money("40.90", "GBP"), f"Expected 40.90, got {new_cost}"
        assert len(parking_groups) == 1, "Expected one parking CostGroup"
        assert parking_groups[0].cost == Money("10.90", "GBP"), (
            f"Parking CostGroup should have cost=Money('10.90', 'GBP'), got {parking_groups[0].cost}"
        )
        assert parking_groups[0].legs[0].mode == LegMode.PARK, "Parking CostGroup should have LegMode.PARK"


# ── School commute ──────────────────────────────────────────────────────


class TestSchoolCommute:
    """compute_school_commute — thin wrapper around get_commute."""

    @pytest.mark.asyncio
    async def test_delegates_to_get_commute(self):
        """compute_school_commute calls get_commute with has_car=False, max_walk_minutes=20."""
        from houses.school import School
        from houses.school_gender import SchoolGender
        from houses.schools import compute_school_commute

        captured = {}

        class _FakeRouter:
            async def get_commute(self, origin, dest, *, has_car, max_walk_minutes=None):
                captured.update(origin=origin, dest=dest, has_car=has_car, max_walk_minutes=max_walk_minutes)
                from dag.attempt import Attempt

                commute = Commute(
                    person=Person(name="", has_car=False),
                    label="",
                    destination=PlaceOfInterest(label="", address=dest),
                    duration=Quantity(10, "minute"),
                    daily_cost=Money("0.0", "GBP"),
                )
                return Attempt.succeeded(commute)

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
        result = await compute_school_commute("SL6 1AA", school, router=_FakeRouter())

        assert result is not None
        assert result.duration.magnitude == 10
        assert captured["has_car"] is False
        assert captured["max_walk_minutes"] == 20
        assert captured["origin"] == "SL6 1AA"
        assert captured["dest"] == "SL6 1AA"


class TestAddressWaypoint:
    """_address_waypoint must handle postcodes, GeoPoints, and coordinate strings."""

    def test_postcode_returns_address_waypoint(self):
        from houses.commute_router import CommuteRouter

        result = CommuteRouter._address_waypoint("SW1V 2QQ")
        assert result.to_dict() == {"address": "SW1V 2QQ"}

    def test_geopoint_returns_location_waypoint(self):
        from houses.commute_router import CommuteRouter
        from houses.geopoint import GeoPoint

        gp = GeoPoint(lat=51.5, lon=-0.13)
        result = CommuteRouter._address_waypoint(gp)
        assert result.to_dict() == {"location": {"latLng": {"latitude": 51.5, "longitude": -0.13}}}

    def test_coordinate_string_returns_location_waypoint(self):
        """'lat,lon' strings must use location format, not address."""
        from houses.commute_router import CommuteRouter

        result = CommuteRouter._address_waypoint("51.5,-0.13")
        assert result.to_dict() == {"location": {"latLng": {"latitude": 51.5, "longitude": -0.13}}}

    def test_invalid_coordinate_string_falls_back_to_address(self):
        from houses.commute_router import CommuteRouter

        result = CommuteRouter._address_waypoint("not-a-coordinate")
        assert result.to_dict() == {"address": "not-a-coordinate"}


class TestGoogleRoutesPostReturn:
    """_google_routes_post must return the response data on cache miss.

    Regression: the function called set_cached() but never returned
    `data`, so every uncached Google Routes POST returned None and the
    caller reported "Google Routes returned no data" even when the API
    succeeded.
    """

    @pytest.mark.asyncio
    async def test_returns_data_on_cache_miss(self):
        from unittest.mock import AsyncMock

        from houses.commute_router import GoogleRoutesClient, GoogleRoutesOptions


        fake_resp = AsyncMock()
        fake_resp.raise_for_status = lambda: None
        fake_resp.status_code = 200
        fake_resp.json = lambda: {"routes": [{"duration": "600s"}]}
        fake_client = AsyncMock()
        fake_client.post = AsyncMock(return_value=fake_resp)

        class _FakeCM:
            async def __aenter__(self):
                return fake_client

            async def __aexit__(self, *a):
                return False

        set_cached_calls = []
        result = await GoogleRoutesClient().post(
            {"x": 1},
            "mask",
            options=GoogleRoutesOptions(
                api_key="fake-key",
                client_factory=lambda **kw: _FakeCM(),
                set_cached_fn=lambda *a, **kw: set_cached_calls.append(a),
            ),
        )

        assert result == {"routes": [{"duration": "600s"}]}, (
            f"Expected response data to be returned, got {result!r}"
        )
        assert len(set_cached_calls) == 1, "set_cached must be called on cache miss"


class TestGoogleRouteCommuteErrorReason:
    """_google_route_commute must preserve the API's error reason."""

    @pytest.mark.asyncio
    async def test_error_reason_included(self):

        import httpx

        from houses.commute_router import CommuteRouter

        async def boom(body, mask, options=None):
            raise httpx.HTTPStatusError(
                "400 Bad Request — LatLng cannot be specified as an Address Waypoint",
                request=httpx.Request("POST", "http://example.com"),
                response=httpx.Response(400, request=httpx.Request("POST", "http://example.com")),
            )

        class _StubClient:
            async def post(self, body, field_mask, *, options=None):
                return await boom(body, field_mask, options=options)

        router = CommuteRouter(routes_client=_StubClient())
        result = await router._google_route_commute("51.5,-0.1", "51.6,-0.2", "WALK", max_walk_minutes=30)
        assert result.impossible
        assert "LatLng cannot be specified" in result.error, f"Got: {result.error}"


class TestGoogleRouteCommuteLegDestination:
    """The walk/drive leg must carry its destination in end_station —
    the DAG is the source of the destination, not the interface."""

    @pytest.mark.asyncio
    async def test_walk_leg_has_destination(self):

        from houses.commute import LegMode
        from houses.commute_router import CommuteRouter

        async def fake_routes(body, mask, options=None):
            return {
                "routes": [
                    {
                        "duration": "900s",
                        "distanceMeters": 1200,
                        "legs": [],
                    }
                ]
            }

        class _StubClient:
            async def post(self, body, field_mask, *, options=None):
                return await fake_routes(body, field_mask, options=options)

        router = CommuteRouter(routes_client=_StubClient())
        result = await router._google_route_commute(
            "51.5,-0.1", "Larchfield Primary School, Bargeman Road, Maidenhead SL6 4ET", "WALK", max_walk_minutes=60
        )
        assert result.succeeded
        commute = result.value_or_none()
        assert commute is not None
        leg = commute.details[0].legs[0]
        assert leg.mode == LegMode.WALK
        assert leg.end_station == "Larchfield Primary School, Bargeman Road, Maidenhead SL6 4ET"

    @pytest.mark.asyncio
    async def test_drive_leg_has_destination(self):

        from houses.commute import LegMode
        from houses.commute_router import CommuteRouter

        async def fake_routes(body, mask, options=None):
            return {
                "routes": [
                    {
                        "duration": "1800s",
                        "distanceMeters": 15000,
                        "legs": [],
                    }
                ]
            }

        class _StubClient:
            async def post(self, body, field_mask, *, options=None):
                return await fake_routes(body, field_mask, options=options)

        router = CommuteRouter(routes_client=_StubClient())
        result = await router._google_route_commute(
            "51.5,-0.1", "Waite House, Doncastle Road, Bracknell RG12 8YA", "DRIVE"
        )
        assert result.succeeded
        commute = result.value_or_none()
        assert commute is not None
        leg = commute.details[0].legs[0]
        assert leg.mode == LegMode.DRIVE
        assert leg.end_station == "Waite House, Doncastle Road, Bracknell RG12 8YA"


class TestGoogleRoutesPostSeam:
    """Regression: the pipeline builder passes ``router.google_routes_post``
    into BusRouteNode; during the parameter-object refactor the public
    property vanished (only the private method remained), so every
    PropertyNodes construction — and therefore app startup — crashed with
    AttributeError. The router must expose the seam publicly again."""

    def test_router_exposes_public_google_routes_post(self):
        from houses.commute_router import CommuteRouter

        router = CommuteRouter()
        assert hasattr(router, "google_routes_post"), (
            "CommuteRouter must expose the google_routes_post seam the "
            "pipeline builder passes to BusRouteNode"
        )

    def test_seam_is_the_bound_post_callable(self):
        from houses.commute_router import CommuteRouter

        router = CommuteRouter()
        assert callable(router.google_routes_post)
        # Bound to the router's own POST implementation.
        assert router.google_routes_post.__name__ == "post"

    def test_builder_can_pass_seam_into_bus_route_node(self):
        """The startup path: build_commute_pipeline reads
        _commute_router().google_routes_post for every person/POI pair."""
        from houses.commute_router import CommuteRouter

        router = CommuteRouter()
        seam = router.google_routes_post  # AttributeError here = the regression
        assert callable(seam)
