from __future__ import annotations

from dag.attempt import Attempt
from dag.computed_node import ComputedNode
from houses.geo import GeoPoint
from houses.model.geo import is_single_property_address


class BestAddressNode(ComputedNode[str]):
    """Selects the best address from available sources.

    Priority: user_entered_address > corrected_address > rightmove_address.
    """

    def __init__(self, node_id: str, *,
                 user_entered_address,
                 corrected_address,
                 rightmove_address):
        super().__init__(
            node_id, str,
            (user_entered_address, corrected_address, rightmove_address),
        )

    def compute(self, user_entered: Attempt[str],
                corrected: Attempt[str],
                rightmove: Attempt[str]) -> Attempt[str]:
        if user_entered.succeeded:
            return user_entered
        if corrected.succeeded:
            return corrected
        if rightmove.succeeded:
            return rightmove
        return self._impossible(
            {"user_entered_address": user_entered,
             "corrected_address": corrected,
             "rightmove_address": rightmove}
        )


class BestLocationNode(ComputedNode[GeoPoint]):
    """Selects the best location from available sources.

    Priority: precise_location > geocode(best_address) > rightmove_location.

    The geocode path is only attempted when best_address resolves to a
    single-property address. Geocoding requires async compute and is
    wired separately (see GeocodeNode).
    """

    def __init__(self, node_id: str, *, precise_location, rightmove_location,
                 best_address):
        super().__init__(
            node_id, GeoPoint, (precise_location, rightmove_location, best_address)
        )

    def compute(self, precise: Attempt[GeoPoint],
                rightmove: Attempt[GeoPoint],
                address: Attempt[str]) -> Attempt[GeoPoint]:
        if precise.succeeded:
            return precise
        if address.succeeded and is_single_property_address(address.value_or_none()):
            return self._impossible(
                {"precise_location": precise, "rightmove_location": rightmove},
                extra=(
                    f"address '{address.value_or_none()}' is single-property "
                    "but geocoding requires async compute"
                ),
            )
        if rightmove.succeeded:
            return rightmove
        return self._impossible(
            {
                "precise_location": precise,
                "rightmove_location": rightmove,
                "best_address": address,
            }
        )
