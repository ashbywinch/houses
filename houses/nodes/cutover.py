"""Push enrichment results into the DAG's UserInputNodes.

The glue between the Rightmove enrichment result and the property's
input nodes; the scheduler cascade and DB persistence take it from there.
"""

from __future__ import annotations

from money import Money

from dag.attempt import AttemptError
from dag.user_input_node import UserInputNode
from houses.geopoint import GeoPoint
from houses.property import EnrichedProperty

_SCRAPE_ERROR_NODES = {
    "address": "rightmove_address",
    "bedrooms": "rightmove_bedrooms",
    "price": "rightmove_price",
    "location": "rightmove_location",
}
"""Scraper parse-error field → the DAG source node that owns it."""


def push_enriched_property(
    rid: str,
    enriched: EnrichedProperty,
    sources: dict[str, UserInputNode],
    scrape_errors: dict[str, str] | None = None,
) -> None:
    """Push an EnrichedProperty's fields to the new DAG UserInputNodes.

    Args:
        rid: Property RID.
        enriched: The EnrichedProperty from enrichment.
        sources: Dict of UserInputNode instances (from a PropertyNodes object).
        scrape_errors: Per-field parse failures from the scrape (Rightmove
            page-model fields whose VALUE could not be interpreted). Each is
            recorded on its source node as an impossible attempt — the DAG's
            error mechanism — so the card renders the reason instead of a
            silent absence. A field the enrichment DID produce (payload/user
            value) is pushed afterwards, which supersedes the failure.
    """
    for field, message in (scrape_errors or {}).items():
        node_key = _SCRAPE_ERROR_NODES.get(field)
        if node_key and node_key in sources:
            sources[node_key].fail(
                message,
                error_info=AttemptError(
                    code="parse_error",
                    source="Rightmove",
                    message=message,
                ),
            )
    if enriched.address:
        sources["rightmove_address"].push(enriched.address, "Rightmove")
    if enriched.url:
        sources["rightmove_url"].push(enriched.url, "Browser extension")
    if enriched.bedrooms is not None:
        sources["rightmove_bedrooms"].push(str(enriched.bedrooms), "Rightmove")
    if enriched.price is not None:
        # Never double-wrap a Money — the enriched price may already be a
        # Money (server.py converts scraped prices); the node needs the
        # amount string either way.
        price = enriched.price if isinstance(enriched.price, Money) else Money(str(enriched.price or "0"), "GBP")
        sources["rightmove_price"].push(price, "Rightmove")
    if enriched.approx_latitude is not None and enriched.approx_longitude is not None:
        sources["rightmove_location"].push(
            GeoPoint(
                lat=enriched.approx_latitude,
                lon=enriched.approx_longitude,
            ),
            "Rightmove map",
        )
    _push_precise_location(enriched, sources)


def _push_precise_location(enriched, sources):
    if enriched.actual_latitude is not None and enriched.actual_longitude is not None and "precise_location" in sources:
        sources["precise_location"].push(
            GeoPoint(lat=enriched.actual_latitude, lon=enriched.actual_longitude),
            "User location",
        )
