"""Tests for transit DAG nodes."""

from __future__ import annotations

from dataclasses import replace

import pytest
from money import Money
from pint import Quantity

from dag.attempt import Attempt
from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode
from houses.commute import CostGroup, JourneyLeg, LegMode
from houses.geopoint import GeoPoint
from houses.model.domain import Commute, Person, PlaceOfInterest
from houses.nodes.transit import RouteOptions, TflTransitNode, TransitOptions


class TestTransitNode:
    @pytest.mark.asyncio
    async def test_pending_without_location(self):
        from houses.nodes.transit import TransitNode

        loc = UserInputNode[GeoPoint]("loc", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)

        no_bus = TflTransitNode(
            "t1nb", options=TransitOptions(best_location=loc, poi=poi, has_car=False, allow_bus=False)
        )
        with_bus = TflTransitNode(
            "t1wb", options=TransitOptions(best_location=loc, poi=poi, has_car=False, allow_bus=True)
        )
        node = TransitNode(
            "tn",
            options=TransitOptions(
                best_location=loc, poi=poi, has_car=False, no_bus_node=no_bus, with_bus_node=with_bus
            ),
        )
        a = await node.attempt()
        assert a.pending

    @pytest.mark.asyncio
    async def test_pending_without_poi(self):
        from houses.nodes.transit import TransitNode

        loc = UserInputNode[GeoPoint]("loc2", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi2", PlaceOfInterest)

        loc.push(GeoPoint(51.5, -0.1), "test")
        await flush_processor()
        no_bus = TflTransitNode(
            "t2nb", options=TransitOptions(best_location=loc, poi=poi, has_car=False, allow_bus=False)
        )
        with_bus = TflTransitNode(
            "t2wb", options=TransitOptions(best_location=loc, poi=poi, has_car=False, allow_bus=True)
        )
        node = TransitNode(
            "tn2",
            options=TransitOptions(
                best_location=loc, poi=poi, has_car=False, no_bus_node=no_bus, with_bus_node=with_bus
            ),
        )
        a = await node.attempt()
        assert a.pending


    async def test_route_failure_has_friendly_user_message(self):
        """A raw TfL error must never reach the UI: the internal message
        keeps it for logs, display_message is the friendly leaf (walkthrough
        run 3 — a raw 'HTTP 404: {$type: ...}' blob was rendered)."""
        from houses.geopoint import GeoPoint
        from houses.model.domain import Commute, PlaceOfInterest
        from houses.nodes.transit import TransitNode

        loc = UserInputNode[GeoPoint]("loc_msg", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi_msg", PlaceOfInterest)
        nb = UserInputNode[Commute]("nb_msg", Commute)
        wb = UserInputNode[Commute]("wb_msg", Commute)
        node = TransitNode(
            "tn_msg",
            options=TransitOptions(
                best_location=loc,
                poi=poi,
                has_car=False,
                no_bus_node=nb,  # type: ignore[arg-type]  # params are annotated TflTransitNode but the runtime only needs a Node dep — compute() is driven directly with canned Attempts here, so UserInputNode placeholders are the test's contract
                with_bus_node=wb,  # type: ignore[arg-type]  # annotated TflTransitNode but only a Node dep is needed at runtime — compute() is called directly with canned Attempts
            ),
        )
        raw = "HTTP 404: {'$type': 'Tfl.Api.Presentation.Entities.ApiError', 'timestampUtc': '...'}"
        a = await node.compute(
            Attempt.succeeded(GeoPoint(51.5, -0.1)),
            Attempt.succeeded(PlaceOfInterest(label="Office", address="1 New Office, London")),
            Attempt.impossible(raw),
            Attempt.impossible(raw),
        )
        assert a.impossible
        assert a.error_info is not None
        assert a.error_info.display_message == "Couldn't find a route to this destination — check the address."
        assert "HTTP 404" in a.error_info.message  # internal message keeps the raw error for logs


class TestWalkLegCheckNode:
    @pytest.mark.asyncio
    async def test_false_when_no_transit(self):
        from houses.commute import CostGroup
        from houses.nodes.transit import WalkLegCheckNode

        transit = UserInputNode[Commute]("transit_w", Commute)
        commute = Commute(
            person=Person("Simon", has_car=True),
            label="Office",
            destination=PlaceOfInterest("Office", "SW1V 2QQ"),
            duration=Quantity(30, "minute"),
            daily_cost=Money("0", "GBP"),
            _details=(CostGroup(legs=(), operator="", cost=None),),  # no legs → no walk
        )
        node = WalkLegCheckNode("wlc", transit_node=transit, max_walk=30)
        transit.push(commute, "test")
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        assert a.value_or_none() is False


class TestTransitNodeJson:
    @pytest.mark.asyncio
    async def test_to_json_has_boolean_fields(self):
        """TransitNode.to_json() must include succeeded/pending/impossible booleans."""
        from houses.nodes.transit import TransitNode

        loc = UserInputNode[GeoPoint]("loc_tj", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi_tj", PlaceOfInterest)

        no_bus = TflTransitNode(
            "tj_nb", options=TransitOptions(best_location=loc, poi=poi, has_car=False, allow_bus=False)
        )
        with_bus = TflTransitNode(
            "tj_wb", options=TransitOptions(best_location=loc, poi=poi, has_car=False, allow_bus=True)
        )
        node = TransitNode(
            "tn_json",
            options=TransitOptions(
                best_location=loc,
                poi=poi,
                has_car=False,
                no_bus_node=no_bus,
                with_bus_node=with_bus,
            ),
        )
        j = await node.to_json()
        assert "succeeded" in j, "Missing succeeded field"
        assert "pending" in j, "Missing pending field"
        assert "impossible" in j, "Missing impossible field"
        assert j["pending"] is True, "Should be pending (no deps pushed)"
        assert j["succeeded"] is False
        assert j["impossible"] is False
        assert j["status"] == "pending"

    @pytest.mark.asyncio
    async def test_provenance_includes_input_values(self):
        """When a transit commute fails, the provenance should include the
        input values (origin coordinates, destination postcode) so developers
        can see what inputs caused the failure without tracing code.

        This relies on UserInputNode.build_provenance() including value,
        and Provenance.to_dict() serializing it.
        """
        from houses.geopoint import GeoPoint
        from houses.model.domain import PlaceOfInterest
        from houses.nodes.transit import TransitNode

        loc = UserInputNode[GeoPoint]("loc_ti", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi_ti", PlaceOfInterest)

        no_bus = TflTransitNode(
            "ti_nb", options=TransitOptions(best_location=loc, poi=poi, has_car=False, allow_bus=False)
        )
        with_bus = TflTransitNode(
            "ti_wb", options=TransitOptions(best_location=loc, poi=poi, has_car=False, allow_bus=True)
        )
        TransitNode(
            "tn_inputs",
            options=TransitOptions(
                best_location=loc,
                poi=poi,
                has_car=False,
                no_bus_node=no_bus,
                with_bus_node=with_bus,
            ),
        )


class TestTransitNodeNoRoute:
    """A transit "no route" answer is a succeeded-infeasible commute; only
    genuine failures are impossible."""

    def _infeasible(self, label: str = "NoRoute"):
        return Commute(
            person=Person(name="", has_car=False),
            label=label,
            destination=PlaceOfInterest(label=label, address="RG12 8YA"),
            duration=Quantity(0, "minute"),
            daily_cost=Money("0", "GBP"),
            mode="transit",
            _details=(),
            infeasible=True,
        )

    def _feasible(self, minutes: int):
        return Commute(
            person=Person(name="", has_car=False),
            label="Route",
            destination=PlaceOfInterest(label="Route", address="SW1V 2QQ"),
            duration=Quantity(minutes, "minute"),
            daily_cost=Money("5", "GBP"),
            mode="transit",
            _details=(),
        )

    async def _pick(self, has_car, no_bus, with_bus):
        from typing import cast

        from houses.nodes.transit import TflTransitNode, TransitNode, TransitOptions

        def _dummy(nid: str) -> TflTransitNode:
            return cast(TflTransitNode, UserInputNode[Commute](nid, Commute))

        loc = UserInputNode[GeoPoint]("nrl_loc", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("nrl_poi", PlaceOfInterest)
        node = TransitNode(
            "nrl",
            options=TransitOptions(
                best_location=loc,
                poi=poi,
                has_car=has_car,
                no_bus_node=_dummy("nrl_nb"),
                with_bus_node=_dummy("nrl_wb"),
            ),
        )
        return await node.compute(
            Attempt.succeeded(GeoPoint(51.5, -0.1)),
            Attempt.succeeded(PlaceOfInterest(label="X", address="SW1V 2QQ")),
            no_bus,
            with_bus,
        )

    @pytest.mark.asyncio
    async def test_no_car_path_carries_no_route_reason(self):
        """The car-less journey is infeasible with a reason the UI can
        show — the review caught the reason being dropped."""
        from houses.model.domain import PlaceOfInterest
        from houses.nodes.transit import DriveNode

        loc = UserInputNode[GeoPoint]("nc_loc", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("nc_poi", PlaceOfInterest)
        node = DriveNode("nc", options=RouteOptions(best_location=loc, poi=poi, has_car=False))
        a = await node.compute(
            Attempt.succeeded(GeoPoint(51.5, -0.1)),
            Attempt.succeeded(PlaceOfInterest(label="X", address="SW1V 2QQ")),
        )
        assert a.succeeded
        v = a.value_or_none()
        assert v is not None and v.infeasible
        assert v.no_route_reason == "no car available"

    @pytest.mark.asyncio
    async def test_has_car_prefers_feasible_no_bus(self):
        a = await self._pick(
            True,
            no_bus=Attempt.succeeded(self._feasible(40)),
            with_bus=Attempt.succeeded(self._feasible(55)),
        )
        assert a.succeeded
        _v = a.value_or_none()
        assert _v is not None
        assert _v.duration.magnitude == 40

    @pytest.mark.asyncio
    async def test_no_bus_infeasible_falls_to_with_bus(self):
        a = await self._pick(
            True,
            no_bus=Attempt.succeeded(self._infeasible("no route")),
            with_bus=Attempt.succeeded(self._feasible(55)),
        )
        assert a.succeeded
        _v = a.value_or_none()
        assert _v is not None
        assert _v.duration.magnitude == 55

    @pytest.mark.asyncio
    async def test_both_infeasible_stays_infeasible(self):
        a = await self._pick(
            False,
            no_bus=Attempt.succeeded(self._infeasible()),
            with_bus=Attempt.succeeded(self._infeasible()),
        )
        assert a.succeeded
        _v = a.value_or_none()
        assert _v is not None
        assert _v.infeasible

    @pytest.mark.asyncio
    async def test_both_genuinely_failed_is_impossible(self):
        a = await self._pick(
            False,
            no_bus=Attempt.impossible("api down"),
            with_bus=Attempt.impossible("api down"),
        )
        assert a.impossible

    @pytest.mark.asyncio
    async def test_no_bus_api_failure_does_not_mask_with_bus(self):
        a = await self._pick(
            False,
            no_bus=Attempt.impossible("api down"),
            with_bus=Attempt.succeeded(self._feasible(55)),
        )
        assert a.succeeded
        _v = a.value_or_none()
        assert _v is not None
        assert _v.duration.magnitude == 55


class TestTflClientNoRoute:
    """TflClient returns succeeded-infeasible for a no-journey response."""

    @pytest.mark.asyncio
    async def test_process_data_without_journey_is_infeasible_not_impossible(self):
        from houses.tfl_client import TflClient, TflRouteOptions

        client = TflClient("SW1V 2QQ", "RG12 8YA", "Bracknell", options=TflRouteOptions(park_and_ride=True))
        a = await client._process_data({})
        assert a.succeeded, f"no-journey answer must be succeeded, got: {a.status}: {a.error}"
        _v = a.value_or_none()
        assert _v is not None
        assert _v.infeasible

    @pytest.mark.asyncio
    async def test_404_http_error_is_infeasible_not_impossible(self):
        """A TfL 404 'No journey found for your inputs' (e.g. the only
        route needs a mode we excluded) is a deterministic no-route
        answer — the client must yield succeeded-infeasible, never an
        impossible attempt that poisons the transit branch."""
        from dag.http_error import HttpError
        from houses.tfl_client import TflClient, TflRouteOptions

        async def raise_404(url, params):
            raise HttpError(
                404,
                message="{'message': 'No journey found for your inputs.'}",
                body="{'message': 'No journey found for your inputs.'}",
            )

        client = TflClient(
            "51.5788804,-0.7648387",
            "RG12 8YA",
            "Bracknell",
            options=TflRouteOptions(park_and_ride=True, cached_call=raise_404),
        )
        data = await client._fetch_data()
        assert data is None
        a = await client._process_data(None)
        assert a.succeeded, f"404 no-route must be succeeded, got: {a.status}: {a.error}"
        _v = a.value_or_none()
        assert _v is not None
        assert _v.infeasible
        # Two-tier messaging: the user-facing reason is clean — the
        # status code and probe strategy live on the provenance.
        assert _v.no_route_reason == "TfL couldn't find a route for this journey"
        assert "HTTP 404" not in _v.no_route_reason
        assert "bus mode excluded" not in _v.no_route_reason

    @pytest.mark.asyncio
    async def test_409_http_error_still_propagates(self):
        """A planner outage (409) is a genuine failure — it must keep
        raising so the DAG retries/surfaces it, not masquerade as no-route."""
        import pytest

        from dag.http_error import HttpError
        from houses.tfl_client import TflClient, TflRouteOptions

        async def raise_409(url, params):
            raise HttpError(409, message="route planner unavailable", body="{}")

        client = TflClient("SW1V 2QQ", "RG12 8YA", "Bracknell", options=TflRouteOptions(cached_call=raise_409))
        with pytest.raises(HttpError) as excinfo:
            await client._fetch_data()
        assert excinfo.value.status == 409

    @pytest.mark.asyncio
    async def test_404_no_route_does_not_poison_transit_chain(self):
        """Full scheduler path: no_bus has no route (succeeded-infeasible
        after the 404 conversion) and with_bus succeeds — TransitNode must
        pick with_bus.  An impossible no_bus would short-circuit refresh
        before compute and poison the branch."""
        from houses.nodes.transit import TflTransitNode, TransitNode, TransitOptions
        from houses.tfl_client import TflClient, TflRouteOptions

        def _infeasible():
            return Commute(
                person=Person(name="", has_car=True),
                label="no route",
                destination=PlaceOfInterest(label="Bracknell", address="RG12 8YA"),
                duration=Quantity(0, "minute"),
                daily_cost=Money("0", "GBP"),
                mode="transit",
                _details=(),
                infeasible=True,
                no_route_reason="TfL couldn't find a route for this journey (HTTP 404, bus mode excluded)",
            )

        def _feasible(minutes: int):
            return Commute(
                person=Person(name="", has_car=True),
                label="Bracknell",
                destination=PlaceOfInterest(label="Bracknell", address="RG12 8YA"),
                duration=Quantity(minutes, "minute"),
                daily_cost=Money("5", "GBP"),
                mode="transit",
                _details=(),
            )

        async def plan(client):
            if client._allow_bus:
                return Attempt.succeeded(_feasible(55))
            return Attempt.succeeded(_infeasible())

        loc = UserInputNode[GeoPoint]("t404_loc", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("t404_poi", PlaceOfInterest)
        loc.push(GeoPoint(51.5, -0.1), "test")
        poi.push(PlaceOfInterest(label="Bracknell", address="RG12 8YA"), "test")

        def make_client(origin, dest, label, options=None):
            opts = options or TflRouteOptions()
            return TflClient(
                origin,
                dest,
                label,
                options=TflRouteOptions(
                    park_and_ride=opts.park_and_ride,
                    allow_bus=opts.allow_bus,
                    plan_override=plan,
                ),
            )

        no_bus = TflTransitNode(
            "t404_nb",
            options=TransitOptions(
                best_location=loc, poi=poi, has_car=True, allow_bus=False, client_factory=make_client
            ),
        )
        with_bus = TflTransitNode(
            "t404_wb",
            options=TransitOptions(
                best_location=loc, poi=poi, has_car=True, allow_bus=True, client_factory=make_client
            ),
        )
        node = TransitNode(
            "t404",
            options=TransitOptions(
                best_location=loc,
                poi=poi,
                has_car=True,
                no_bus_node=no_bus,
                with_bus_node=with_bus,
            ),
        )

        await flush_processor()
        a = await node.attempt()
        assert a.succeeded, f"transit must pick with_bus, got: {a.status}: {a.error}"
        _v = a.value_or_none()
        assert _v is not None
        assert _v.duration.magnitude == 55
        # The no-route reason is debuggable from the provenance of the
        # no_bus probe — a plain description, not a log side-channel.
        nb_prov = await no_bus.build_provenance()
        assert "HTTP 404" in (nb_prov.description or "")

class TestNationalRailFallback:
    """TransitNode's National Rail fallback — routes origins beyond TfL
    coverage (property 90691101, Hungerford: TfL 404s from every origin
    form) via the wired transit_route_fn (Google Routes TRANSIT)."""

    def _commute(self, minutes: int = 122) -> Commute:
        """A feasible National Rail journey the router would return."""
        legs = (
            JourneyLeg(mode=LegMode.WALK, duration=Quantity(15, "minute")),
            JourneyLeg(
                mode=LegMode.TRAIN,
                duration=Quantity(67, "minute"),
                line_name="GWR",
                end_station="London Paddington Rail Station",
            ),
            JourneyLeg(mode=LegMode.TUBE, duration=Quantity(26, "minute"), line_name="Victoria"),
            JourneyLeg(mode=LegMode.WALK, duration=Quantity(5, "minute")),
        )
        return Commute(
            person=Person(name="", has_car=False),
            label="Pimlico",
            destination=PlaceOfInterest(label="Pimlico", address="1 Drummond Gate, Pimlico, London SW1V 2QQ"),
            duration=Quantity(minutes, "minute"),
            daily_cost=Money("0", "GBP"),
            mode="transit",
            _details=(CostGroup(legs=legs, operator="TfL", cost=None),),
        )

    async def _run(self, fallback, no_bus=None, with_bus=None, has_car=False):
        from typing import cast

        from houses.nodes.transit import TflTransitNode, TransitNode, TransitOptions

        def _dummy(nid: str) -> TflTransitNode:
            return cast(TflTransitNode, UserInputNode[Commute](nid, Commute))

        loc = UserInputNode[GeoPoint]("nrf_loc", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("nrf_poi", PlaceOfInterest)
        options = TransitOptions(
            best_location=loc,
            poi=poi,
            has_car=has_car,
            no_bus_node=_dummy("nrf_nb"),
            with_bus_node=_dummy("nrf_wb"),
        )
        if fallback is not None:
            options = TransitOptions(
                best_location=loc,
                poi=poi,
                has_car=has_car,
                no_bus_node=_dummy("nrf_nb"),
                with_bus_node=_dummy("nrf_wb"),
                transit_route_fn=fallback,
            )
        node = TransitNode("nrf", options=options)
        return await node.compute(
            Attempt.succeeded(GeoPoint(51.415344, -1.511056)),
            Attempt.succeeded(PlaceOfInterest(label="Pimlico", address="SW1V 2QQ")),
            no_bus or Attempt.succeeded(self._infeasible_commute()),
            with_bus or Attempt.succeeded(self._infeasible_commute()),
        )

    @staticmethod
    def _infeasible_commute() -> Commute:
        return Commute(
            person=Person(name="", has_car=False),
            label="NoRoute",
            destination=PlaceOfInterest(label="NoRoute", address="RG17 0LA"),
            duration=Quantity(0, "minute"),
            daily_cost=Money("0", "GBP"),
            mode="transit",
            _details=(),
            infeasible=True,
            no_route_reason="TfL couldn't find a route for this journey",
        )

    @pytest.mark.asyncio
    async def test_tfl_infeasible_falls_back_to_national_rail(self):
        """Both TfL variants succeeded-infeasible + a wired fallback →
        the National Rail journey wins (regression: Hungerford)."""
        async def fake_route(loc, dest):
            return self._commute()

        a = await self._run(fake_route)
        assert a.succeeded, f"National Rail fallback must produce a journey, got {a.status}: {a.error}"
        _v = a.value_or_none()
        assert _v is not None and not _v.infeasible
        assert _v.duration.magnitude == 122
        modes = [leg.mode for cg in _v.details for leg in cg.legs]
        assert LegMode.TRAIN in modes, "the fallback journey must include the train leg"

    @pytest.mark.asyncio
    async def test_fallback_returning_none_keeps_infeasible(self):
        """The router said 'no route' → the node keeps the
        succeeded-infeasible result so the selector can try drive/walk."""
        async def fake_route(loc, dest):
            return None

        a = await self._run(fake_route)
        assert a.succeeded
        _v = a.value_or_none()
        assert _v is not None and _v.infeasible

    @pytest.mark.asyncio
    async def test_fallback_failure_keeps_infeasible(self):
        """A Google failure must never crash the node nor mask the
        drive/walk fallback."""
        async def fake_route(loc, dest):
            raise RuntimeError("google down")

        a = await self._run(fake_route)
        assert a.succeeded
        _v = a.value_or_none()
        assert _v is not None and _v.infeasible

    @pytest.mark.asyncio
    async def test_fallback_provenance_narrates_both_steps(self):
        """The provenance says why TfL lost and what replaced it."""
        from houses.nodes.transit import TransitNode, TransitOptions

        async def fake_route(loc, dest):
            return self._commute()

        loc = UserInputNode[GeoPoint]("nrfp_loc", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("nrfp_poi", PlaceOfInterest)
        node = TransitNode(
            "nrfp",
            options=TransitOptions(
                best_location=loc,
                poi=poi,
                no_bus_node=UserInputNode[Commute]("nrfp_nb", Commute),
                with_bus_node=UserInputNode[Commute]("nrfp_wb", Commute),
                transit_route_fn=fake_route,
            ),
        )
        loc.push(GeoPoint(51.415344, -1.511056), "geocode")
        poi.push(PlaceOfInterest(label="Pimlico", address="SW1V 2QQ"), "persons_source")
        # Drive the node directly through compute (same seam as _run) then
        # assert the provenance description narrates the fallback.
        await node.compute(
            Attempt.succeeded(GeoPoint(51.415344, -1.511056)),
            Attempt.succeeded(PlaceOfInterest(label="Pimlico", address="SW1V 2QQ")),
            Attempt.succeeded(self._infeasible_commute()),
            Attempt.succeeded(self._infeasible_commute()),
        )

        p = await node.build_provenance()
        assert p.description is not None
        assert "National Rail fallback" in p.description
        assert "TfL" in p.description

    @pytest.mark.asyncio
    async def test_fallback_accepts_string_poi_like_the_real_pipeline(self):
        """The builder's poi input is a UserInputNode[str] holding the
        postcode (only schools arrive as PlaceOfInterest) — the fallback
        must normalize the string, not crash (live regression: the box
        logged 'str' object has no attribute 'address')."""
        from houses.nodes.transit import TransitNode, TransitOptions

        received = {}

        async def fake_route(loc, dest):
            received["dest"] = dest
            return self._commute()

        loc = UserInputNode[GeoPoint]("nrfs_loc", GeoPoint)
        poi = UserInputNode[str]("nrfs_poi", str)
        node = TransitNode(
            "nrfs",
            options=TransitOptions(
                best_location=loc,
                poi=poi,
                no_bus_node=UserInputNode[Commute]("nrfs_nb", Commute),
                with_bus_node=UserInputNode[Commute]("nrfs_wb", Commute),
                transit_route_fn=fake_route,
            ),
        )
        a = await node.compute(
            Attempt.succeeded(GeoPoint(51.415344, -1.511056)),
            Attempt.succeeded("1 Drummond Gate, Pimlico, London SW1V 2QQ"),
            Attempt.succeeded(self._infeasible_commute()),
            Attempt.succeeded(self._infeasible_commute()),
        )
        assert a.succeeded
        _v = a.value_or_none()
        assert _v is not None and not _v.infeasible
        assert isinstance(received["dest"], PlaceOfInterest)
        assert received["dest"].address == "1 Drummond Gate, Pimlico, London SW1V 2QQ"

    @pytest.mark.asyncio
    async def test_plain_tfl_success_clears_fallback_narration(self):
        """After a fallback run, a later compute where TfL succeeds must
        NOT still narrate the National Rail fallback — provenance
        describes the run that actually happened (PR #68 review)."""
        from houses.nodes.transit import TransitNode, TransitOptions

        async def fake_route(loc, dest):
            return self._commute()

        loc = UserInputNode[GeoPoint]("nrfn_loc", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("nrfn_poi", PlaceOfInterest)
        node = TransitNode(
            "nrfn",
            options=TransitOptions(
                best_location=loc,
                poi=poi,
                no_bus_node=UserInputNode[Commute]("nrfn_nb", Commute),
                with_bus_node=UserInputNode[Commute]("nrfn_wb", Commute),
                transit_route_fn=fake_route,
            ),
        )
        # 1) TfL infeasible → fallback used; provenance narrates it.
        a = await node.compute(
            Attempt.succeeded(GeoPoint(51.415344, -1.511056)),
            Attempt.succeeded(PlaceOfInterest(label="Pimlico", address="SW1V 2QQ")),
            Attempt.succeeded(self._infeasible_commute()),
            Attempt.succeeded(self._infeasible_commute()),
        )
        assert a.succeeded
        v = a.value_or_none()
        assert v is not None and not v.infeasible
        p = await node.build_provenance()
        assert "National Rail fallback" in (p.description or "")

        # 2) TfL succeeds this time → the fallback narration must be gone.
        a2 = await node.compute(
            Attempt.succeeded(GeoPoint(51.415344, -1.511056)),
            Attempt.succeeded(PlaceOfInterest(label="Pimlico", address="SW1V 2QQ")),
            Attempt.succeeded(self._commute()),
            Attempt.succeeded(self._commute()),
        )
        assert a2.succeeded
        v2 = a2.value_or_none()
        assert v2 is not None and not v2.infeasible
        p2 = await node.build_provenance()
        assert "National Rail fallback" not in (p2.description or ""), (
            "a plain TfL success must not narrate a fallback that did not run"
        )

    @pytest.mark.asyncio
    async def test_fallback_carries_the_poi_destination(self):
        """The fallback commute must get the same label/destination
        fixups as the normal path — the summary/provenance shows the
        POI label + trips, not a raw postcode (PR #68 review)."""
        from houses.nodes.transit import TransitNode, TransitOptions

        poi_info = PlaceOfInterest(
            label="Pimlico",
            address="1 Drummond Gate, Pimlico, London SW1V 2QQ",
            trips_per_week=5,
        )

        async def fake_route(loc, dest):
            # The real router labels str POIs with "" — the node must
            # derive the label from its own id, like the TfL path.
            return replace(self._commute(), label="")

        loc = UserInputNode[GeoPoint]("nrfp3_loc", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("nrfp3_poi", PlaceOfInterest)
        node = TransitNode(
            "90691101/Simon/Pimlico/computed_transit",
            options=TransitOptions(
                best_location=loc,
                poi=poi,
                no_bus_node=UserInputNode[Commute]("nrfp3_nb", Commute),
                with_bus_node=UserInputNode[Commute]("nrfp3_wb", Commute),
                poi_info=poi_info,
                transit_route_fn=fake_route,
            ),
        )
        a = await node.compute(
            Attempt.succeeded(GeoPoint(51.415344, -1.511056)),
            Attempt.succeeded(PlaceOfInterest(label="Pimlico", address="1 Drummond Gate, Pimlico, London SW1V 2QQ")),
            Attempt.succeeded(self._infeasible_commute()),
            Attempt.succeeded(self._infeasible_commute()),
        )
        assert a.succeeded
        v = a.value_or_none()
        assert v is not None and not v.infeasible
        assert v.label == "Pimlico", "the fallback label must come from the node id"
        assert v.destination.label == "Pimlico"
        assert v.destination.trips_per_week == 5
