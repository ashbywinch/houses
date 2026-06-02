"""Transit commute, petrol cost, and school lookup logic.

Stub implementations — enrichment logic to be filled in once transport
and school data sources are confirmed.
"""

from houses.config import settings
from houses.models import PetrolCost, SchoolInfo, TransitInfo


# ---------------------------------------------------------------------------
# Transit
# ---------------------------------------------------------------------------

async def compute_transit(
    origin_postcode: str,
    destination_postcode: str,
    label: str,
) -> TransitInfo:
    """Return transit commute time from origin to destination.

    Uses public-transport baselines (not driving).
    Stub that returns None until a transit data source is wired up.
    """
    # TODO: call TfL API / Google Maps Distance Matrix / OpenRouteService
    return TransitInfo(
        destination_label=label,
        destination_postcode=destination_postcode,
        duration_minutes=None,
        mode="transit",
    )


async def compute_simon_commute(property_postcode: str) -> TransitInfo:
    """Transit time from property to Simon's work anchor (Pimlico/Victoria)."""
    return await compute_transit(
        property_postcode,
        settings.simon_postcode,
        "Simon — Pimlico / Victoria",
    )


async def compute_lorena_commute(property_postcode: str) -> TransitInfo:
    """Transit time from property to Lorena's work anchor (Aldgate)."""
    return await compute_transit(
        property_postcode,
        settings.lorena_postcode,
        "Lorena — Aldgate / City of London",
    )


# ---------------------------------------------------------------------------
# Petrol
# ---------------------------------------------------------------------------

def compute_petrol_cost(origin_postcode: str) -> PetrolCost:
    """Estimate round-trip petrol cost to the Bracknell office.

    Uses 45 mpg, £1.45/L.
    Stub that returns None until a distance API is wired up.
    """
    # TODO: call routing API for driving distance
    return PetrolCost(
        round_trip_km=None,
        cost_gbp=None,
    )


# ---------------------------------------------------------------------------
# Schools
# ---------------------------------------------------------------------------

async def find_nearest_boys_primary(postcode: str) -> SchoolInfo | None:
    """Find the closest non-fee-paying primary school that accepts boys.

    Stub implementation.
    """
    # TODO: query school registry / API
    return None


async def find_nearest_boys_secondary(postcode: str) -> SchoolInfo | None:
    """Find the closest non-fee-paying secondary school that accepts boys.

    If the nearest secondary is girls-only, substitute the nearest
    co-educational or boys-only alternative.
    """
    # TODO: query school registry / API; apply gender substitution rule
    return None
