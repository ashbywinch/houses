from __future__ import annotations

import logging

from dag.attempt import Attempt, Provenance
from dag.computed_node import ComputedNode
from houses.geo import GeoPoint
from houses.schools import SchoolGender

logger = logging.getLogger(__name__)


class PrimarySchoolNode(ComputedNode[dict]):
    """Async node that finds the nearest primary school via the school service."""

    def __init__(self, node_id: str, *, best_location, best_address):
        super().__init__(node_id, dict, (best_location, best_address))

    async def compute(self, location: Attempt[GeoPoint],
                      address_attempt: Attempt[str]) -> Attempt[dict]:
        from houses.context import get_services

        if not location.is_succeeded:
            return self._impossible({"best_location": location})
        addr = address_attempt.value_or_none() if address_attempt.is_succeeded else ""
        school = await get_services().school_lookup.find_nearest(
            f"{location.value_or_none().lat},{location.value_or_none().lon}",
            child_age=7, requirement=SchoolGender.BOYS, address=addr,
        )
        if school:
            loc = location.value_or_none()
            school_url = school.url or (
                f"https://get-information-schools.service.gov.uk/Establishments/Establishment/Details/{school.urn}"
                if school.urn else ""
            )
            return Attempt.succeeded(
                {"name": school.name, "ofsted": school.ofsted_rating or "",
                 "postcode": school.postcode, "distance_km": _distance_km(loc, school),
                 "url": school_url, "coords": school.coords or loc},
                Provenance("GIAS CSV", description=f"primary school: {school.name}"),
            )
        return Attempt.impossible("no primary school found",
                                   Provenance("GIAS CSV", description="no school near property"))


class SecondarySchoolNode(ComputedNode[dict]):
    """Async node that finds the nearest secondary school from CSV data."""

    def __init__(self, node_id: str, *, best_location, best_address):
        super().__init__(node_id, dict, (best_location, best_address))

    async def compute(self, location: Attempt[GeoPoint],
                      address_attempt: Attempt[str]) -> Attempt[dict]:
        from houses.context import get_services

        if not location.is_succeeded:
            return self._impossible({"best_location": location})
        addr = address_attempt.value_or_none() if address_attempt.is_succeeded else ""
        school = await get_services().school_lookup.find_nearest(
            f"{location.value_or_none().lat},{location.value_or_none().lon}",
            child_age=12, requirement=SchoolGender.BOYS, address=addr,
        )
        if school:
            loc = location.value_or_none()
            school_url = school.url or (
                f"https://get-information-schools.service.gov.uk/Establishments/Establishment/Details/{school.urn}"
                if school.urn else ""
            )
            return Attempt.succeeded(
                {"name": school.name, "ofsted": school.ofsted_rating or "",
                 "postcode": school.postcode, "distance_km": _distance_km(loc, school),
                 "url": school_url, "coords": school.coords or loc},
                Provenance("GIAS CSV", description=f"secondary school: {school.name}"),
            )
        return Attempt.impossible("no secondary school found",
                                   Provenance("GIAS CSV", description="no school near property"))


class SchoolLocationNode(ComputedNode[GeoPoint]):
    """Returns the school's coordinates from a school node.

    Deps: (school_node — PrimarySchoolNode or SecondarySchoolNode)
    The signal chain: school_node computes → changed → SchoolLocationNode
    stale → recompute → TransitNode stale → recompute commute.
    """

    def __init__(self, node_id: str, *, school_node):
        super().__init__(node_id, GeoPoint, (school_node,))

    async def compute(self, school_attempt: Attempt[dict]) -> Attempt[GeoPoint]:
        if not school_attempt.is_succeeded:
            return self._impossible({"school": school_attempt})
        val = school_attempt.value_or_none()
        coords = val.get("coords")
        if isinstance(coords, GeoPoint):
            return Attempt.succeeded(coords, Provenance("school_location"))
        return Attempt.impossible("no coords in school data",
                                   Provenance("school_data",
                                              description=f"school {val.get('name')} has no coords"))


def _distance_km(loc: GeoPoint, school) -> float:
    if school.coords:
        return loc.distance_km_to(school.coords)
    return 0.0
