"""Pydantic models for property payload and enriched data."""

from pydantic import BaseModel


class PropertyPayload(BaseModel):
    """Raw payload extracted by Page Assist from a Rightmove listing."""

    url: str
    postcode: str
    bedrooms: int
    price: float


class TransitInfo(BaseModel):
    """Transit commute details for a single person."""

    destination_label: str
    destination_postcode: str
    duration_minutes: int | None = None
    mode: str = "transit"


class SchoolInfo(BaseModel):
    """School details for a single school."""

    name: str
    type: str  # "primary" or "secondary"
    distance_km: float | None = None
    gender: str = "mixed"
    fee_paying: bool = False
    walking_time_minutes: int | None = None


class PetrolCost(BaseModel):
    """Estimated petrol cost for a round trip."""

    destination: str = "Bracknell Office (RG12 8YA)"
    round_trip_km: float | None = None
    cost_gbp: float | None = None


class EnrichedProperty(BaseModel):
    """Full enriched property record written to the Google Sheet."""

    url: str
    postcode: str
    bedrooms: int
    price: float

    # Commute enrichment
    simon_commute: TransitInfo | None = None
    lorena_commute: TransitInfo | None = None

    # Bracknell petrol
    petrol: PetrolCost | None = None

    # Schools
    primary_school: SchoolInfo | None = None
    secondary_school: SchoolInfo | None = None
