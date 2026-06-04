"""Pydantic models for property payload and enriched data."""

from pydantic import BaseModel


class PropertyPayload(BaseModel):
    """Raw payload extracted by Page Assist from a Rightmove listing.

    Only `url` is required. The LLM should extract `address` (the street
    address line) and `postcode` (the full UK postcode if visible on the
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


class ReprocessRequest(BaseModel):
    """Request to re-enrich existing properties by Rightmove ID.

    Omit ``ids`` to re-enrich every row in the sheet that has the
    necessary data for the requested fields.
    """

    ids: list[str] | None = None


class TransitInfo(BaseModel):
    """Transit commute details for a single person."""

    destination_label: str
    destination_postcode: str
    duration_minutes: int | None = None
    daily_cost_gbp: float | None = None
    mode: str = "transit"


class SchoolInfo(BaseModel):
    """School details for a single school."""

    name: str
    type: str  # "primary" or "secondary"
    distance_km: float | None = None
    gender: str = "mixed"
    fee_paying: bool = False
    walking_time_minutes: int | None = None
    bus_time_minutes: int | None = None
    bus_route: str = ""
    urn: str = ""
    website: str = ""
    ofsted_rating: str = ""
    inspection_year: str = ""
    inspection_summary: str = ""


class PetrolCost(BaseModel):
    """Estimated petrol cost for a round trip."""

    destination: str = "Bracknell Office (RG12 8YA)"
    round_trip_km: float | None = None
    round_trip_minutes: int | None = None
    cost_gbp: float | None = None


class CouncilTaxInfo(BaseModel):
    """Council tax band, cost, and evidence source."""

    band: str = ""
    yearly_cost: float | None = None
    evidence_url: str = ""


class CommuteBreakdown(BaseModel):
    """Individual daily costs plus yearly total."""

    simon_daily_gbp: float | None = None
    lorena_daily_gbp: float | None = None
    bracknell_daily_gbp: float | None = None
    yearly_total_gbp: float | None = None
    formula_explanation: str = ""


class EnrichedProperty(BaseModel):
    """Full enriched property record written to the Google Sheet."""

    url: str
    address: str = ""
    postcode: str = ""
    bedrooms: int = 0
    price: float = 0.0

    # Commute enrichment
    simon_commute: TransitInfo | None = None
    lorena_commute: TransitInfo | None = None

    # Bracknell petrol
    petrol: PetrolCost | None = None

    # Schools
    primary_school: SchoolInfo | None = None
    secondary_school: SchoolInfo | None = None

    town_description: str = ""
    walk_to_town_minutes: int | None = None
    walkable_amenities: str = ""
    primary_ofsted: str = ""
    secondary_ofsted: str = ""
    primary_inspection_year: str = ""
    primary_inspection_summary: str = ""
    secondary_inspection_year: str = ""
    secondary_inspection_summary: str = ""
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
