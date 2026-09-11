from __future__ import annotations

from dataclasses import dataclass
from typing import override

from dag.attempt import Attempt, SourceType
from dag.derived_node import DerivedNode
from dag.node import Node
from houses.geopoint import GeoPoint
from houses.school_gender import SchoolGender
from houses.services_provider import get_services


@dataclass(frozen=True)
class _SchoolJson:
    """Wire shape of the nearest-school value — lat/lon included only
    when the school has coordinates."""

    name: str
    ofsted: str
    walk: None
    url: str
    postcode: str
    full_address: str
    lat: float | None = None
    lon: float | None = None
    lat: float | None = None
    lon: float | None = None

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction IS the serialization boundary (coding-standards.md)
        d: dict[str, object] = dict(
            name=self.name,
            ofsted=self.ofsted,
            walk=self.walk,
            url=self.url,
            postcode=self.postcode,
            full_address=self.full_address,
        )
        if self.lat is not None:
            d["lat"] = self.lat
            d["lon"] = self.lon
        return d


class NearestSchoolNode(DerivedNode[dict]):
    """Nearest-school lookup shared by the primary and secondary stages.

    Subclasses supply the child age and the stage name used in failure
    messages; the lookup itself is identical.
    """

    child_age: int
    stage: str

    provenance_source_type = SourceType.API

    def __init__(self, node_id: str, *, best_location, best_address, acceptable: tuple[str, ...] = ("mixed",)):
        deps: tuple[Node, ...] = (best_location, best_address)
        super().__init__(node_id, dict, deps)
        self._acceptable: tuple[str, ...] = acceptable

    @override
    async def compute(self, location: Attempt[GeoPoint], address: Attempt[str]) -> Attempt[dict]:
        loc = location.value_or_none()
        if loc is None:
            return self._impossible({"location": location})
        svc = get_services()
        attempt = await svc.school_lookup.find_nearest(
            f"{loc.lat},{loc.lon}",
            child_age=self.child_age,
            acceptable=tuple(SchoolGender(v) for v in self._acceptable),
        )
        if attempt.pending:
            return Attempt.pending()
        if attempt.impossible:
            # Propagate the real reason (e.g. geocoding failed) — don't
            # collapse it into a generic "no school found".
            return Attempt.impossible(attempt.error or f"no {self.stage} school found")
        school = attempt.value_or_none()
        if school is None:
            return Attempt.impossible(f"no {self.stage} school found within search radius")
        return Attempt.succeeded(
            _SchoolJson(
                name=school.name,
                ofsted=school.ofsted_rating,
                walk=None,
                url=school.url,
                postcode=school.postcode,
                full_address=school.full_address,
                lat=school.coords.lat if school.coords else None,
                lon=school.coords.lon if school.coords else None,
            ).to_dict()
        )


class PrimarySchoolNode(NearestSchoolNode):
    """Nearest primary school (age-4 entry)."""

    child_age = 4
    stage = "primary"


class SecondarySchoolNode(NearestSchoolNode):
    """Nearest secondary school (age-12 entry)."""

    child_age = 12
    stage = "secondary"


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
