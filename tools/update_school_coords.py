"""One-shot: geocode each school's full address and store corrected coordinates.

Reads ``data/edubaseall_enriched.csv``, adds ``CorrectedLatitude`` and
``CorrectedLongitude`` columns, then for each school whose existing
coordinates are within ~200 km of London, geocodes the school's full
address (name + street + locality + town + postcode) to get the actual
building location.

Skips schools that already have corrected coordinates — resumable if
the script is interrupted.

Writes to a temp file then atomically renames, so concurrent readers
never see a partially-written CSV.

Usage::

    uv run python tools/update_school_coords.py
"""

from __future__ import annotations

import asyncio
import csv
import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from houses.geo import GeoPoint
from houses.location import _geocode_address, _get_geo_state

logger = logging.getLogger(__name__)

CSV_PATH = Path("data/edubaseall_enriched.csv")
LONDON = GeoPoint(51.5074, -0.1278)
MAX_KM = 200


def _atomic_write(rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    """Write CSV to a temp file then atomically replace the original."""
    tmp = CSV_PATH.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="latin-1") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(CSV_PATH)


def _build_full_address(row: dict[str, Any]) -> str:
    name = (row.get("EstablishmentName") or "").strip()
    street = (row.get("Street") or "").strip()
    locality = (row.get("Locality") or "").strip()
    town = (row.get("Town") or "").strip()
    county = (row.get("County (name)") or "").strip()
    postcode = (row.get("Postcode") or "").strip()
    return ", ".join(p for p in (name, street, locality, town, county, postcode) if p)


def _existing_coords(row: dict[str, Any]) -> GeoPoint | None:
    lat = (row.get("Latitude") or "").strip()
    lng = (row.get("Longitude") or "").strip()
    if lat and lng:
        try:
            return GeoPoint(float(lat), float(lng))
        except (ValueError, TypeError):
            return None
    return None


def _near_london(row: dict[str, Any]) -> bool:
    coords = _existing_coords(row)
    if coords is None:
        return True
    return coords.distance_km_to(LONDON) <= MAX_KM


def _already_done(row: dict[str, Any]) -> bool:
    lat = (row.get("CorrectedLatitude") or "").strip()
    lng = (row.get("CorrectedLongitude") or "").strip()
    return bool(lat and lng)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    if not CSV_PATH.is_file():
        logger.error("CSV not found: %s", CSV_PATH)
        sys.exit(1)

    with CSV_PATH.open(newline="", encoding="latin-1") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows: list[dict[str, Any]] = list(reader)

    for col in ("CorrectedLatitude", "CorrectedLongitude"):
        if col not in fieldnames:
            fieldnames.append(col)

    total = len(rows)
    skipped = far_away = done = failed = 0

    logger.info("Processing %d schools...", total)

    for i, row in enumerate(rows):
        if _already_done(row):
            skipped += 1
            continue
        if not _near_london(row):
            far_away += 1
            continue

        address = _build_full_address(row)
        if not address:
            logger.warning("Row %d: empty address, skipping", i)
            continue

        try:
            result = await _geocode_address(address)
            if result.succeeded:
                pt = result.value_or_none()
                if pt is not None:
                    row["CorrectedLatitude"] = f"{pt.lat:.7f}"
                    row["CorrectedLongitude"] = f"{pt.lon:.7f}"
                    done += 1
            else:
                failed += 1
                logger.debug("Row %d (%s): geocode failed", i, address[:60])
        except Exception as e:
            failed += 1
            logger.debug("Row %d (%s): error: %s", i, address[:60], e)

        if _get_geo_state().nominatim_exhausted:
            logger.info("Nominatim exhausted — stopping (done=%d failed=%d)", done, failed)
            break

        if (i + 1) % 100 == 0:
            logger.info("  Progress: %d/%d (done=%d skipped=%d far=%d failed=%d)",
                        i + 1, total, done, skipped, far_away, failed)

        await asyncio.sleep(0.1)
        if done > 0:
            _atomic_write(rows, fieldnames)

    if done > 0:
        _atomic_write(rows, fieldnames)

    logger.info("Complete: done=%d skipped=%d far=%d failed=%d / %d total",
                done, skipped, far_away, failed, total)


if __name__ == "__main__":
    asyncio.run(main())
