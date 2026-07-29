"""Cutover bridge — push enriched data to the new DAG's UserInputNodes,
and migrate old ashby_works float to works_estimates dict.

This module contains the glue code that pushes enrichment results to the
new dag/ library's UserInputNodes. It runs alongside the existing
write_enriched_row() calls and old DAG persistence.

No existing enrichment modules, sheet writes, or houses/sheets/ are modified.
"""

from __future__ import annotations

import logging

from money import Money

from dag.persistence import latest_node_result
from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode
from houses.geo import GeoPoint
from houses.property import EnrichedProperty

logger = logging.getLogger(__name__)


def push_enriched_property(
    rid: str, enriched: EnrichedProperty, sources: dict[str, UserInputNode]
) -> None:
    """Push an EnrichedProperty's fields to the new DAG UserInputNodes.

    Args:
        rid: Property RID.
        enriched: The EnrichedProperty from enrichment.
        sources: Dict of UserInputNode instances (from a PropertyNodes object).
    """
    if enriched.address:
        sources["rightmove_address"].push(enriched.address, "Rightmove")
    if enriched.url:
        sources["rightmove_url"].push(enriched.url, "Browser extension")
    if enriched.bedrooms is not None:
        sources["rightmove_bedrooms"].push(str(enriched.bedrooms), "Rightmove")
    if enriched.price is not None:
        sources["rightmove_price"].push(
            Money(str(enriched.price or "0"), "GBP"), "Rightmove"
        )
    if enriched.approx_latitude is not None and enriched.approx_longitude is not None:
            sources["rightmove_location"].push(
                GeoPoint(
                    lat=enriched.approx_latitude,
                    lon=enriched.approx_longitude,
                ),
                "Rightmove map",
            )
    if enriched.postcode and "postcode" in sources:
        sources["postcode"].push(enriched.postcode, "Rightmove")
    if (
        enriched.actual_latitude is not None
        and enriched.actual_longitude is not None
        and "precise_location" in sources
    ):
        sources["precise_location"].push(
            GeoPoint(
                lat=enriched.actual_latitude, lon=enriched.actual_longitude
            ),
            "User location",
        )
    if enriched.actual_postcode and "actual_postcode" in sources:
        sources["actual_postcode"].push(enriched.actual_postcode, "Rightmove")


def migrate_old_ashby_works_sync(property_nodes) -> None:
    """Synchronous version — pushes old ashby_works float to works_estimates dict.

    Called during PropertyNodes construction. Does not flush the DAG processor
    (not needed for correctness — subsequent pushes will trigger recomputation).
    """
    rid = property_nodes.rid
    old_key = f"{rid}/ashby_works"
    old_result = latest_node_result(old_key)

    if old_result and old_result.get("status") == "succeeded":
        old_value = old_result.get("value")
        if old_value is not None and isinstance(old_value, (int, float)):
            property_nodes.works_estimates.push(
                {"Ashby": float(old_value)}, "migration"
            )
            logger.info(
                "Migrated ashby_works=%.2f for RID %s to works_estimates",
                float(old_value),
                rid,
            )


async def migrate_old_ashby_works(property_nodes) -> None:
    """Async version — migrates and flushes the DAG processor."""
    migrate_old_ashby_works_sync(property_nodes)
    await flush_processor()
