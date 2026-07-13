from __future__ import annotations

from dag.attempt import Attempt, Provenance
from dag.derived_node import DerivedNode
from dag.node import Node
from houses.context import get_services
from houses.geo import GeoPoint


class PrimarySchoolNode(DerivedNode[dict]):
    def __init__(self, node_id: str, *, best_location, best_address):
        deps: tuple[Node, ...] = (best_location, best_address)
        super().__init__(node_id, dict, deps)

    async def compute(self, location: Attempt[GeoPoint],
                      address: Attempt[str]) -> Attempt[dict]:
        if not location.succeeded:
            return self._impossible({"best_location": location})
        loc = location.value_or_none()
        svc = get_services()
        school = await svc.school_lookup.find_nearest(
            f"{loc.lat},{loc.lon}", child_age=4,
        )
        if school is None:
            return Attempt.impossible("no primary school found")
        return Attempt.succeeded({
            "name": school.name,
            "ofsted": school.ofsted_rating,
            "walk_minutes": None,
        })

    async def build_provenance(self):
        return Provenance(label="GIAS CSV")


class SecondarySchoolNode(DerivedNode[dict]):
    def __init__(self, node_id: str, *, best_location, best_address):
        deps: tuple[Node, ...] = (best_location, best_address)
        super().__init__(node_id, dict, deps)

    async def compute(self, location: Attempt[GeoPoint],
                      address: Attempt[str]) -> Attempt[dict]:
        if not location.succeeded:
            return self._impossible({"best_location": location})
        loc = location.value_or_none()
        svc = get_services()
        school = await svc.school_lookup.find_nearest(
            f"{loc.lat},{loc.lon}", child_age=11,
        )
        if school is None:
            return Attempt.impossible("no secondary school found")
        return Attempt.succeeded({
            "name": school.name,
            "ofsted": school.ofsted_rating,
            "walk_minutes": None,
        })

    async def build_provenance(self):
        return Provenance(label="GIAS CSV")


class SchoolLocationNode(DerivedNode[str]):
    def __init__(self, node_id: str, *, school_node):
        super().__init__(node_id, str, (school_node,))

    def compute(self, school: Attempt[dict]) -> Attempt[str]:
        if not school.succeeded:
            return self._impossible({"school_node": school})
        return Attempt.succeeded("school_location")

    async def build_provenance(self):
        return Provenance(label="school_location")
