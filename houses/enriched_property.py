"""The enriched property record written to the Google Sheet."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from money import Money
from pint import Quantity

from houses.commute import CommuteBreakdown
from houses.council_tax_info import CouncilTaxInfo
from houses.school import School

if TYPE_CHECKING:
    from houses.model.domain import Commute


@dataclass
class EnrichedProperty:
    """Full enriched property record written to the Google Sheet."""

    url: str
    rid: str = ""
    address: str = ""
    bedrooms: int | None = 0
    price: Money | None = Money(amount="0", currency="GBP")

    # Commute enrichment
    simon_commute: Commute | None = None
    lorena_commute: Commute | None = None

    # Bracknell commute (driving)
    petrol: Commute | None = None

    # Schools
    primary_school: School | None = None
    primary_school_commute: Commute | None = None
    primary_school_distance: Quantity | None = None
    secondary_school: School | None = None
    secondary_school_commute: Commute | None = None
    secondary_school_distance: Quantity | None = None

    town_description: str = ""
    walk_to_town: Quantity | None = None
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

    # Cached approximate values (from geocoding)
    approx_latitude: float | None = None
    approx_longitude: float | None = None
    approx_station_crs: str = ""
    approx_station_name: str = ""

    # Separate geocode values captured BEFORE the rightmove override
    geocode_latitude: float | None = None
    geocode_longitude: float | None = None
    geo_provenance: str = ""
