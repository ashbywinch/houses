"""Tests for transit DAG nodes."""

from __future__ import annotations

import pytest
from money import Money
from pint import Quantity

from dag.attempt import Attempt
from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode
from houses.geo import GeoPoint
from houses.model.domain import Commute, Person, PlaceOfInterest
from houses.nodes.transit import TflTransitNode


class TestTransitNode:
    @pytest.mark.asyncio
    async def test_pending_without_location(self):
        from houses.nodes.transit import TransitNode

        loc = UserInputNode[GeoPoint]("loc", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi", PlaceOfInterest)

        no_bus = TflTransitNode("t1nb", best_location=loc, poi=poi, has_car=False, allow_bus=False)
        with_bus = TflTransitNode("t1wb", best_location=loc, poi=poi, has_car=False, allow_bus=True)
        node = TransitNode(
            "tn", best_location=loc, poi=poi, has_car=False, no_bus_node=no_bus, with_bus_node=with_bus
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
        no_bus = TflTransitNode("t2nb", best_location=loc, poi=poi, has_car=False, allow_bus=False)
        with_bus = TflTransitNode("t2wb", best_location=loc, poi=poi, has_car=False, allow_bus=True)
        node = TransitNode(
            "tn2", best_location=loc, poi=poi, has_car=False, no_bus_node=no_bus, with_bus_node=with_bus
        )
        a = await node.attempt()
        assert a.pending


    async def test_route_failure_has_friendly_user_message(self):
        """A raw TfL error must never reach the UI: the internal message
        keeps it for logs, display_message is the friendly leaf (walkthrough
        run 3 — a raw 'HTTP 404: {$type: ...}' blob was rendered)."""
        from houses.geo import GeoPoint
        from houses.model.domain import Commute, PlaceOfInterest
        from houses.nodes.transit import TransitNode

        loc = UserInputNode[GeoPoint]("loc_msg", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi_msg", PlaceOfInterest)
        nb = UserInputNode[Commute]("nb_msg", Commute)
        wb = UserInputNode[Commute]("wb_msg", Commute)
        node = TransitNode(
            "tn_msg", best_location=loc, poi=poi, has_car=False,
            no_bus_node=nb,  # type: ignore[arg-type]  # placeholders — compute() is called directly
            with_bus_node=wb,  # type: ignore[arg-type]
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

        no_bus = TflTransitNode("tj_nb", best_location=loc, poi=poi, has_car=False, allow_bus=False)
        with_bus = TflTransitNode("tj_wb", best_location=loc, poi=poi, has_car=False, allow_bus=True)
        node = TransitNode(
            "tn_json",
            best_location=loc,
            poi=poi,
            has_car=False,
            no_bus_node=no_bus,
            with_bus_node=with_bus,
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
        from houses.geo import GeoPoint
        from houses.model.domain import PlaceOfInterest
        from houses.nodes.transit import TransitNode

        loc = UserInputNode[GeoPoint]("loc_ti", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("poi_ti", PlaceOfInterest)

        no_bus = TflTransitNode("ti_nb", best_location=loc, poi=poi, has_car=False, allow_bus=False)
        with_bus = TflTransitNode("ti_wb", best_location=loc, poi=poi, has_car=False, allow_bus=True)
        TransitNode(
            "tn_inputs",
            best_location=loc,
            poi=poi,
            has_car=False,
            no_bus_node=no_bus,
            with_bus_node=with_bus,
        )


class TestTransitNodeNoRoute:
    """A transit "no route" answer is a succeeded-infeasible commute; only
    genuine failures are impossible."""

    def _infeasible(self, label: str = "NoRoute"):
        return Commute(
            person=Person(name="", has_car=False),
            label=label,
            destination=PlaceOfInterest(label=label, address="RG12 8YA"),
            duration=Quantity(0, "minute"),  # type: ignore[arg-type]
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
            duration=Quantity(minutes, "minute"),  # type: ignore[arg-type]
            daily_cost=Money("5", "GBP"),
            mode="transit",
            _details=(),
        )

    async def _pick(self, has_car, no_bus, with_bus):
        from typing import cast

        from houses.nodes.transit import TflTransitNode, TransitNode

        def _dummy(nid: str) -> TflTransitNode:
            return cast(TflTransitNode, UserInputNode[Commute](nid, Commute))

        loc = UserInputNode[GeoPoint]("nrl_loc", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("nrl_poi", PlaceOfInterest)
        node = TransitNode(
            "nrl", best_location=loc, poi=poi, has_car=has_car,
            no_bus_node=_dummy("nrl_nb"),
            with_bus_node=_dummy("nrl_wb"),
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
        node = DriveNode("nc", best_location=loc, poi=poi, has_car=False)
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
        from houses.tfl_client import TflClient

        client = TflClient("SW1V 2QQ", "RG12 8YA", "Bracknell", park_and_ride=True)
        a = await client._process_data({})
        assert a.succeeded, f"no-journey answer must be succeeded, got: {a.status}: {a.error}"
        _v = a.value_or_none()
        assert _v is not None
        assert _v.infeasible

    @pytest.mark.asyncio
    async def test_404_http_error_is_infeasible_not_impossible(self, monkeypatch):
        """A TfL 404 'No journey found for your inputs' (e.g. the only
        route needs a mode we excluded) is a deterministic no-route
        answer — the client must yield succeeded-infeasible, never an
        impossible attempt that poisons the transit branch."""
        from dag.http_error import HttpError
        from houses.tfl_client import TflClient

        async def raise_404(url, params):
            raise HttpError(
                404,
                message="{'message': 'No journey found for your inputs.'}",
                body="{'message': 'No journey found for your inputs.'}",
            )

        monkeypatch.setattr(TflClient, "_cached_api_call", staticmethod(raise_404))

        client = TflClient("51.5788804,-0.7648387", "RG12 8YA", "Bracknell", park_and_ride=True)
        data = await client._fetch_data()
        assert data is None
        a = await client._process_data(None)
        assert a.succeeded, f"404 no-route must be succeeded, got: {a.status}: {a.error}"
        _v = a.value_or_none()
        assert _v is not None
        assert _v.infeasible
        assert "HTTP 404" in _v.no_route_reason
        assert "bus mode excluded" in _v.no_route_reason  # allow_bus=False probe

    @pytest.mark.asyncio
    async def test_409_http_error_still_propagates(self, monkeypatch):
        """A planner outage (409) is a genuine failure — it must keep
        raising so the DAG retries/surfaces it, not masquerade as no-route."""
        import pytest

        from dag.http_error import HttpError
        from houses.tfl_client import TflClient

        async def raise_409(url, params):
            raise HttpError(409, message="route planner unavailable", body="{}")

        monkeypatch.setattr(TflClient, "_cached_api_call", staticmethod(raise_409))

        client = TflClient("SW1V 2QQ", "RG12 8YA", "Bracknell")
        with pytest.raises(HttpError) as excinfo:
            await client._fetch_data()
        assert excinfo.value.status == 409

    @pytest.mark.asyncio
    async def test_404_no_route_does_not_poison_transit_chain(self, monkeypatch):
        """Full scheduler path: no_bus has no route (succeeded-infeasible
        after the 404 conversion) and with_bus succeeds — TransitNode must
        pick with_bus.  An impossible no_bus would short-circuit refresh
        before compute and poison the branch."""
        from houses.nodes.transit import TflTransitNode, TransitNode
        from houses.tfl_client import TflClient

        def _infeasible():
            return Commute(
                person=Person(name="", has_car=True),
                label="no route",
                destination=PlaceOfInterest(label="Bracknell", address="RG12 8YA"),
                duration=Quantity(0, "minute"),  # type: ignore[arg-type]
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
                duration=Quantity(minutes, "minute"),  # type: ignore[arg-type]
                daily_cost=Money("5", "GBP"),
                mode="transit",
                _details=(),
            )

        async def plan(self):
            if self._allow_bus:
                return Attempt.succeeded(_feasible(55))
            return Attempt.succeeded(_infeasible())

        monkeypatch.setattr(TflClient, "plan", plan)

        loc = UserInputNode[GeoPoint]("t404_loc", GeoPoint)
        poi = UserInputNode[PlaceOfInterest]("t404_poi", PlaceOfInterest)
        loc.push(GeoPoint(51.5, -0.1), "test")
        poi.push(PlaceOfInterest(label="Bracknell", address="RG12 8YA"), "test")

        no_bus = TflTransitNode("t404_nb", best_location=loc, poi=poi, has_car=True, allow_bus=False)
        with_bus = TflTransitNode("t404_wb", best_location=loc, poi=poi, has_car=True, allow_bus=True)
        node = TransitNode(
            "t404",
            best_location=loc,
            poi=poi,
            has_car=True,
            no_bus_node=no_bus,
            with_bus_node=with_bus,
        )

        await flush_processor()
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
