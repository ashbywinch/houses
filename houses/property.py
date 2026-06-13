"""Everything about a house — property payload, enrichment result, value objects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from houses.commute import Commute, CommuteBreakdown
from houses.schools import School

if TYPE_CHECKING:
    from houses.geo import GeoPoint


@dataclass(frozen=False)
class Property:
    """Raw property listing extracted by Page Assist from a Rightmove page.

    Only ``url`` is required. The LLM should extract ``address`` (the street
    address line) and ``postcode`` (the full UK postcode if visible on the
    page) separately. If no full postcode is found, the server will try
    to extract it from the address.
    """

    url: str
    address: str = ""
    postcode: str = ""
    bedrooms: int | None = None
    price: float | None = None
    tab: str = "Properties Data"
    actual_latitude: float | None = None
    actual_longitude: float | None = None
    actual_postcode: str = ""

    # ── Location resolution ─────────────────────────────────────────

    async def location(self) -> GeoPoint | None:
        """Return the best spatial coordinate for this property."""
        from houses.location import resolve_house_location

        return await resolve_house_location(
            postcode=self.postcode,
            address=self.address,
            actual_latitude=self.actual_latitude,
            actual_longitude=self.actual_longitude,
            approx_lat=None,
            approx_lng=None,
        )


@dataclass(frozen=True)
class CouncilTaxInfo:
    """Council tax band, cost, and evidence source."""

    band: str = ""
    yearly_cost: float | None = None
    evidence_url: str = ""


@dataclass
class EnrichedProperty:
    """Full enriched property record written to the Google Sheet."""

    url: str
    address: str = ""
    postcode: str = ""
    bedrooms: int = 0
    price: float = 0.0

    # Commute enrichment
    simon_commute: Commute | None = None
    lorena_commute: Commute | None = None

    # Bracknell commute (driving)
    petrol: Commute | None = None

    # Schools
    primary_school: School | None = None
    primary_school_commute: Commute | None = None
    primary_school_distance_km: float | None = None
    secondary_school: School | None = None
    secondary_school_commute: Commute | None = None
    secondary_school_distance_km: float | None = None

    town_description: str = ""
    walk_to_town_minutes: int | None = None
    walkable_amenities: str = ""
    primary_ofsted: str = ""
    secondary_ofsted: str = ""
    primary_inspection_year: str = ""
    secondary_inspection_year: str = ""
    epc_rating: str = ""

    council_tax: CouncilTaxInfo | None = None

    commute_breakdown: CommuteBreakdown | None = None

    # User-provided overrides (from Actual Latitude/Longitude columns)
    actual_latitude: float | None = None
    actual_longitude: float | None = None
    actual_postcode: str = ""

    # Cached approximate values (from geocoding)
    approx_latitude: float | None = None
    approx_longitude: float | None = None
    approx_station_crs: str = ""
    approx_station_name: str = ""
