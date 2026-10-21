"""The school's address as a route destination."""

from __future__ import annotations

from typing import override

from dag.attempt import Attempt
from dag.derived_node import DerivedNode


class SchoolLocationNode(DerivedNode[str]):
    """The school's ADDRESS (name + postcode) as the route destination.

    The walk/transit route planners need a geocodable destination string;
    the address is what the legs should display — never a bare lat/lon.
    """

    def __init__(self, node_id: str, *, school_node):
        super().__init__(node_id, str, (school_node,))

    @override
    def compute(self, school: Attempt[dict]) -> Attempt[str]:
        if not school.succeeded:
            return self._impossible({"school_node": school})
        val = school.value_or_none()
        if not val:
            return Attempt.impossible("school has no details")
        # The address captured when the school was first found from the
        # data — the destination the legs display, never a bare lat/lon.
        # The school name leads, joined with a comma: the leg info shows
        # "School Name, Street, Town, Postcode".
        name = (val.get("name") or "").strip()
        full = (val.get("full_address") or "").strip()
        if name and full:
            return Attempt.succeeded(f"{name}, {full}")
        if full:
            return Attempt.succeeded(full)
        postcode = val.get("postcode") or ""
        if name and postcode:
            return Attempt.succeeded(f"{name}, {postcode}")
        if "lat" in val and "lon" in val:
            return Attempt.succeeded(f"{val['lat']},{val['lon']}")
        return Attempt.impossible("school has no address or coordinates")
