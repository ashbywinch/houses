from houses.geo import GeoPoint
from houses.location import _geocode_address
from houses.model.geo import _deserialize_gp, is_single_property_address
from houses.model.registry import NodeKind, node

node(
    id="rid",
    kind=NodeKind.source,
    provenance_template="Derived",
)(lambda: None)

node(
    id="geocode_location",
    kind=NodeKind.source,
    provenance_template="Geocoded",
)(lambda: None)

node(
    id="corrected_address",
    kind=NodeKind.user_input,
    provenance_template="User correction",
    user_table="user_corrected_address",
)(lambda: None)

node(
    id="precise_location",
    kind=NodeKind.user_input,
    provenance_template="User location",
    user_table="user_precise_location",
)(lambda: None)


@node(id="best_address", kind=NodeKind.derived, deps=["corrected_address", "rightmove_address"])
def best_address(
    corrected_address: str | None, rightmove_address: str | None
) -> tuple[str | None, str]:
    if corrected_address:
        return corrected_address, "User correction"
    if rightmove_address:
        return rightmove_address, "Rightmove"
    return None, ""


@node(
    id="best_location",
    kind=NodeKind.derived,
    deps=["precise_location", "rightmove_location", "best_address"],
)
async def best_location(
    precise_location: GeoPoint | str | None,
    rightmove_location: GeoPoint | str | None,
    best_address: str | None,
    _geocoder=None,
) -> tuple[GeoPoint | None, str]:
    if precise_location:
        gp = precise_location if isinstance(precise_location, GeoPoint) else _deserialize_gp(precise_location)
        if gp:
            return gp, "User location"
    if is_single_property_address(best_address):
        geocode = _geocoder or _geocode_address
        point = await geocode(best_address)
        if point and point.is_succeeded:
            coords = point.value_or_none()
            if coords:
                return coords, f"Geocoded ({point.source})"
    if rightmove_location:
        gp = rightmove_location if isinstance(rightmove_location, GeoPoint) else _deserialize_gp(rightmove_location)
        if gp:
            return gp, "Rightmove map"
    return None, ""


@node(id="map_url", kind=NodeKind.derived, deps=["best_location"])
def map_url(best_location: GeoPoint | None) -> tuple[str | None, str]:
    if best_location is not None:
        return f"https://www.google.com/maps?q={best_location.lat},{best_location.lon}", "Computed"
    return None, ""
