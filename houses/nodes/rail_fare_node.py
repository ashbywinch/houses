from __future__ import annotations

from dataclasses import replace

from money import Money

from dag.attempt import Attempt, SourceType
from dag.derived_node import DerivedNode
from houses.commute import LegMode
from houses.geo import GeoPoint
from houses.model.domain import Commute
from houses.stations import Station


class RailFareNode(DerivedNode[Commute]):
    @property
    def provenance_source_type(self) -> SourceType:
        return SourceType.API

    """Computes National Rail fare for a commute when TfL has no price.

    Extracts the destination London terminal from the TfL route legs
    (the last heavy-rail leg), then looks up the fare from the property's
    nearest station to that terminal.  This avoids geocoding POI postcodes
    to find stations — the NR fare system uses terminal zones (PAD, VIC,
    WAT, …) as destinations, and the route already tells us which one.
    """

    def __init__(self, node_id: str, *, transit_result, best_location):
        self.transit_result = transit_result
        self.best_location = best_location
        super().__init__(node_id, Commute, (transit_result,))

    def _get_active_deps(self):
        deps = [self.transit_result]
        transit_attempt = self.transit_result.latest_attempt()
        if transit_attempt.succeeded:
            val = transit_attempt.value_or_none()
            if val is not None and float(val.daily_cost.amount) == 0:
                deps.append(self.best_location)
        return tuple(deps)

    async def compute(self, transit_attempt: Attempt[Commute], location: Attempt[GeoPoint] = None) -> Attempt[Commute]:
        if not transit_attempt.succeeded:
            return Attempt.impossible("transit not succeeded")
        commute = transit_attempt.value_or_none()
        if commute is None:
            return Attempt.impossible("transit value is None")

        # If already has a fare, pass through
        if float(commute.daily_cost.amount) > 0:
            return transit_attempt

        return await self._enrich_rail_fare(commute, location)

    async def _enrich_rail_fare(self, commute: Commute, location: Attempt[GeoPoint]) -> Attempt[Commute]:
        """Core fare-lookup and merge logic.

        Looks up the NR fare from the property's nearest station to the
        terminal station found in the commute legs, then applies it.
        """
        if not location or not location.succeeded:
            return Attempt.impossible("best_location not available")

        from houses.rail_fare_registry import get_rail_fare_registry
        from houses.tfl_client import TflClient

        registry = get_rail_fare_registry()

        origin = registry.nearest_station(location.value_or_none())
        if not origin:
            return Attempt.impossible("origin station not found near property")

        terminal_station = self._find_terminal_station(registry, commute)
        if terminal_station is None:
            return Attempt.impossible("terminal station not found in route legs")

        fare = registry.fare_between(origin, terminal_station)
        if fare is None:
            dummy_lon = Station("London Terminals", "LON", GeoPoint(0, 0))
            fare = registry.fare_between(origin, dummy_lon)
        if fare is None:
            return Attempt.impossible(f"no fare {origin.crs}→{terminal_station.crs}")
        tube_fare = await TflClient.get_tube_leg_fare(terminal_station, "") or Money(
            TflClient.FALLBACK_TUBE_SINGLE_GBP, "GBP"
        )
        total = (fare + tube_fare) * 2

        return self._apply_fare_to_commute(commute, total)

    def _find_terminal_station(self, registry, commute: Commute) -> Station | None:
        """Find the terminal (destination) station from the commute's transit legs."""
        _transit_modes = {LegMode.TRAIN, LegMode.TUBE, LegMode.DLR, LegMode.OVERGROUND}
        for cg in reversed(commute.details):
            for leg in reversed(cg.legs):
                mode_name = leg.mode.name.lower()
                if mode_name in ("train", "tube", "dlr", "overground") and leg.end_station:
                    stn = registry.find_station(leg.end_station)
                    if stn:
                        return stn
        return None

    def _apply_fare_to_commute(self, commute: Commute, total: Money) -> Attempt[Commute]:
        """Apply the computed NR fare to the commute's CostGroups."""
        _transit_modes = {LegMode.TRAIN, LegMode.TUBE, LegMode.DLR, LegMode.OVERGROUND}
        new_details = list(commute.details)
        for i, cg in enumerate(new_details):
            if cg.operator == "TfL" or any(leg.mode in _transit_modes for leg in cg.legs):
                new_details[i] = replace(cg, cost=total)
        new_commute = replace(commute, daily_cost=Money(str(total.amount), "GBP"), details=tuple(new_details))
        return Attempt.succeeded(new_commute)
