from __future__ import annotations

from dag.attempt import Attempt, Provenance
from dag.derived_node import DerivedNode
from dag.node import Node
from houses.geo import GeoPoint
from houses.school_gender import SchoolGender
from houses.services_provider import get_services


class PrimarySchoolNode(DerivedNode[dict]):
    def __init__(self, node_id: str, *, best_location, best_address, acceptable: tuple[str, ...] = ("mixed",)):
        deps: tuple[Node, ...] = (best_location, best_address)
        super().__init__(node_id, dict, deps)
        self._acceptable = acceptable

    async def compute(self, location: Attempt[GeoPoint], address: Attempt[str]) -> Attempt[dict]:
        loc = location.value_or_none()
        if loc is None:
            return self._impossible({"location": location})
        svc = get_services()
        attempt = await svc.school_lookup.find_nearest(
            f"{loc.lat},{loc.lon}",
            child_age=4,
            acceptable=tuple(SchoolGender(v) for v in self._acceptable),
        )
        if attempt.pending:
            return Attempt.pending()
        school = attempt.value_or_none()
        if school is None:
            return Attempt.impossible("no primary school found")
        result = {
            "name": school.name,
            "ofsted": school.ofsted_rating,
            "walk_minutes": None,
            "url": school.url,
        }
        if school.coords:
            result["lat"] = school.coords.lat
            result["lon"] = school.coords.lon
        return Attempt.succeeded(result)


class SecondarySchoolNode(DerivedNode[dict]):
    def __init__(self, node_id: str, *, best_location, best_address, acceptable: tuple[str, ...] = ("mixed",)):
        deps: tuple[Node, ...] = (best_location, best_address)
        super().__init__(node_id, dict, deps)
        self._acceptable = acceptable

    async def compute(self, location: Attempt[GeoPoint], address: Attempt[str]) -> Attempt[dict]:
        loc = location.value_or_none()
        if loc is None:
            return self._impossible({"location": location})
        svc = get_services()
        attempt = await svc.school_lookup.find_nearest(
            f"{loc.lat},{loc.lon}",
            child_age=12,
            acceptable=tuple(SchoolGender(v) for v in self._acceptable),
        )
        if attempt.pending:
            return Attempt.pending()
        school = attempt.value_or_none()
        if school is None:
            return Attempt.impossible("no secondary school found")
        result = {
            "name": school.name,
            "ofsted": school.ofsted_rating,
            "walk_minutes": None,
            "url": school.url,
        }
        if school.coords:
            result["lat"] = school.coords.lat
            result["lon"] = school.coords.lon
        return Attempt.succeeded(result)

    async def build_provenance(self):
        return Provenance(label="GIAS CSV", url="https://get-information-schools.service.gov.uk/")


class SchoolLocationNode(DerivedNode[str]):
    def __init__(self, node_id: str, *, school_node):
        super().__init__(node_id, str, (school_node,))

    def compute(self, school: Attempt[dict]) -> Attempt[str]:
        if not school.succeeded:
            return self._impossible({"school_node": school})
        val = school.value_or_none()
        if val and "lat" in val and "lon" in val:
            return Attempt.succeeded(f"{val['lat']},{val['lon']}")
        return Attempt.impossible("school has no coordinates")
