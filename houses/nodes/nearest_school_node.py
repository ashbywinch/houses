"""Nearest-school lookup for one stage, with its wire shape."""

from __future__ import annotations

from typing import override

from pydantic import SerializerFunctionWrapHandler, model_serializer

from dag.attempt import Attempt, SourceType
from dag.derived_node import DerivedNode
from dag.node import Node
from houses.geopoint import GeoPoint
from houses.school_gender import SchoolGender
from houses.services_provider import get_services
from serialization import SerializableAsDict


class _School(SerializableAsDict):
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

    @model_serializer(mode="wrap")
    def _serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, str | float | None]:
        """A school without coordinates emits the six address keys and
        never a null coordinate pair."""
        d = handler(self)
        if self.lat is None:
            d.pop("lat", None)
            d.pop("lon", None)
        return d


class NearestSchoolNode(DerivedNode[dict]):
    """Nearest-school lookup for one stage (age and stage name are
    constructor config — primary and secondary differ only there).
    """

    provenance_source_type = SourceType.API

    def __init__(
        self,
        node_id: str,
        *,
        best_location,
        child_age: int,
        stage: str,
        acceptable: Node | None = None,
    ):
        self.child_age: int = child_age
        self.stage: str = stage
        deps: list[Node] = [best_location]
        if acceptable is not None:
            deps.append(acceptable)
        super().__init__(node_id, dict, tuple(deps))
        self._acceptable_source: Node | None = acceptable

    @override
    async def compute(
        self, location: Attempt[GeoPoint], acceptable: Attempt[tuple[str, ...]] | None = None
    ) -> Attempt[dict]:
        # The property's best_address is NOT read here — a correction to
        # it must not re-run the school lookup (Part F 4).
        loc = location.value_or_none()
        if loc is None:
            return self._impossible({"location": location})
        acc_val = acceptable.value_or_none() if acceptable is not None else ("mixed",)
        svc = get_services()
        attempt = await svc.school_lookup.find_nearest(
            f"{loc.lat},{loc.lon}",
            child_age=self.child_age,
            acceptable=tuple(SchoolGender(v) for v in (acc_val or ("mixed",))),
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
            _School(
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
