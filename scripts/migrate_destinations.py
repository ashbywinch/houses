"""Migrate POI destinations from postcode to full address.

Pushes updated addresses through the persons_source DAG node so that
downstream commute nodes are marked stale and recompute automatically.

Usage:
    uv run python scripts/migrate_destinations.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dag.scheduler import flush_processor
from houses.model.domain import Person, PlaceOfInterest
from houses.services import Services
from houses.services_provider import get_services


class _DestinationMigration:
    """The destination migration's address table: POI label → full address."""

    NEW_ADDRESSES: dict[str, str] = {
        "Pimlico": "1 Drummond Gate, Pimlico, London SW1V 2QQ",
        "Bracknell": "Waite House, Doncastle Road, Bracknell, Berkshire RG12 8YA",
        "Dad": "Flat 37, Watson Place, Trinity Road, Chipping Norton OX7 5GZ",
        "Aldgate": "Eastgate House, 40 Dukes Place, Aldgate, London EC3A 7LP",
    }


def _persons_to_migrate(persons_node) -> list[Person]:
    """The persons whose POI addresses this migration rewrites.

    [] — with the reason printed — when persons_source has not succeeded
    or holds no persons.
    """
    current = persons_node.latest_attempt()
    persons: list[Person] = (current.value_or_none() or []) if current.succeeded else []
    if not persons:
        print(f"persons_source holds no persons (status={current.status}) — nothing to migrate.")
        return []
    return persons


async def migrate() -> None:
    # Clear cache so _make_settings_source loads fresh from DB
    Services.SETTINGS_SOURCE_CACHE.clear()
    svc = get_services()
    persons_node = svc.persons_source

    persons = _persons_to_migrate(persons_node)
    if not persons:  # _persons_to_migrate printed why there is nothing to do
        return

    updated = 0
    for person in persons:
        new_pois = []
        for poi in person.places_of_interest:
            if poi.label in _DestinationMigration.NEW_ADDRESSES:
                new_poi = PlaceOfInterest(
                    label=poi.label,
                    address=_DestinationMigration.NEW_ADDRESSES[poi.label],
                    trips_per_week=poi.trips_per_week,
                    weeks_per_year=poi.weeks_per_year,
                )
                new_pois.append(new_poi)
                updated += 1
                print(
                    f"  {person.name}/{poi.label}: {poi.label} → "
                    f"{_DestinationMigration.NEW_ADDRESSES[poi.label][:40]}..."
                )
            else:
                new_pois.append(poi)
        object.__setattr__(person, "places_of_interest", tuple(new_pois))

    if not updated:
        print("No POIs matched — nothing to migrate.")
        return

    # Push via DAG so changed signal fires and downstream nodes go stale
    persons_node.push(persons, "migrate")
    print(f"\nPushed {updated} updated POI(s) to persons_source. Flushing processor...")
    await flush_processor()
    await flush_processor()
    print("Migration complete — downstream commute nodes recomputed.")


asyncio.run(migrate())
