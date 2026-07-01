"""Cutover bridge — push enriched data to the new DAG's SourceNodes.

This module contains the glue code that pushes enrichment results to the
new dag/ library's SourceNodes. It runs alongside the existing
write_enriched_row() calls and old DAG persistence.

No existing enrichment modules, sheet writes, or houses/sheets/ are modified.
"""

from __future__ import annotations

import logging

from dag.source_node import SourceNode
from houses.geo import GeoPoint
from houses.property import EnrichedProperty

logger = logging.getLogger(__name__)


def push_enriched_property(rid: str, enriched: EnrichedProperty,
                           sources: dict[str, SourceNode]) -> None:
    """Push an EnrichedProperty's fields to the new DAG SourceNodes.

    Args:
        rid: Property RID.
        enriched: The EnrichedProperty from enrichment.
        sources: Dict of SourceNode instances (from a PropertyNodes object).
    """

    if enriched.address:
        sources["rightmove_address"].push(enriched.address, "Rightmove")
    if enriched.url:
        sources["rightmove_url"].push(enriched.url, "Browser extension")
    if enriched.bedrooms is not None:
        sources["rightmove_bedrooms"].push(str(enriched.bedrooms), "Rightmove")
    if enriched.price is not None:
        sources["rightmove_price"].push(str(enriched.price), "Rightmove")
    if enriched.approx_latitude is not None and enriched.approx_longitude is not None:
        sources["rightmove_location"].push(
            GeoPoint(lat=enriched.approx_latitude, lon=enriched.approx_longitude),
            "Rightmove map",
        )
