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

from houses.geopoint import GeoPoint
from houses.location import geocode_address, get_geo_state

logger = logging.getLogger(__name__)

CSV_PATH = Path("data/edubaseall_enriched.csv")
# lucidlint: ignore magic-number the longitude datum of the LONDON constant — the literal IS the datum
# lucidlint: ignore magic-number coordinate data of the named LONDON constant — the literal IS the datum, naming it
LONDON = GeoPoint(51.5074, -0.1278)
MAX_KM = 200
MAX_CORRECTION_KM = 100
PROGRESS_SAVE_INTERVAL = 100
NOMINATIM_DELAY_S = 0.15


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _atomic_write(rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    """Write CSV to a temp file then atomically replace the original."""
    tmp = CSV_PATH.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="latin-1") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(CSV_PATH)


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _build_full_address(row: dict[str, Any]) -> str:
    name = (row.get("EstablishmentName") or "").strip()
    street = (row.get("Street") or "").strip()
    locality = (row.get("Locality") or "").strip()
    town = (row.get("Town") or "").strip()
    postcode = (row.get("Postcode") or "").strip()
    # Exclude county — historic names like "Middlesex" confuse the
    # ORS-Pelias geocoder and cause it to return the UK centroid.
    return ", ".join(p for p in (name, street, locality, town, postcode) if p)


def _existing_coords(row: dict[str, Any]) -> GeoPoint | None:
    """Parse original GIAS Latitude/Longitude from a CSV row."""
    lat = (row.get("Latitude") or "").strip()
    lng = (row.get("Longitude") or "").strip()
    if lat and lng:
        try:
            return GeoPoint(float(lat), float(lng))
        except (ValueError, TypeError):
            return None
    return None


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _clear_coords(row: dict[str, Any]) -> None:
    """Remove corrected coords from a row (mutates in place)."""
    row["CorrectedLatitude"] = ""
    row["CorrectedLongitude"] = ""


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _near_london(row: dict[str, Any]) -> bool:
    coords = _existing_coords(row)
    if coords is None:
        return True
    return coords.distance_km_to(LONDON) <= MAX_KM


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _already_done(row: dict[str, Any]) -> bool:
    """True when existing corrected coords are present AND pass the 100km sanity check.

    Invalid coords (e.g. UK centroid from failed ORS-Pelias lookups) are
    cleared and treated as not-done so the script re-processes them.
    """
    lat = (row.get("CorrectedLatitude") or "").strip()
    lng = (row.get("CorrectedLongitude") or "").strip()
    if not (lat and lng):
        return False
    try:
        corrected = GeoPoint(float(lat), float(lng))
    except (ValueError, TypeError):
        _clear_coords(row)
        return False
    original = _existing_coords(row)
    if original is not None and original.distance_km_to(corrected) >= MAX_CORRECTION_KM:
        _clear_coords(row)
        return False
    return True


async def _geocode_address_with_fallback(address: str, row: dict[str, Any]) -> GeoPoint | None:
    """Geocode the full address; retry with name + postcode when it fails.

    Some schools have streets/locality in GIAS that don't match what geocoding
    services know, so the fallback keeps those rows findable.
    """
    result = await geocode_address(address)
    if not (result.succeeded and result.value_or_none() is not None):
        name = (row.get("EstablishmentName") or "").strip()
        postcode = (row.get("Postcode") or "").strip()
        fallback = f"{name}, {postcode}" if name and postcode else ""
        if fallback:
            logger.debug("Full address failed, retrying with '%s'", fallback)
            result = await geocode_address(fallback)
    return result.value_or_none()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    if not CSV_PATH.is_file():
        logger.error("CSV not found: %s", CSV_PATH)
        sys.exit(1)

    with CSV_PATH.open(newline="", encoding="latin-1") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows: list[dict[str, Any]] = list(reader)

    fieldnames.extend(col for col in ("CorrectedLatitude", "CorrectedLongitude") if col not in fieldnames)

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

        pt = await _geocode_address_with_fallback(address, row)
        if pt is not None:
            row["CorrectedLatitude"] = f"{pt.lat:.7f}"
            row["CorrectedLongitude"] = f"{pt.lon:.7f}"
            done += 1
        else:
            failed += 1
            logger.debug("Row %d (%s): geocode failed", i, address[:60])

        if get_geo_state().nominatim_exhausted:
            logger.info("Nominatim exhausted — stopping (done=%d failed=%d)", done, failed)
            break

        if (i + 1) % PROGRESS_SAVE_INTERVAL == 0:
            _atomic_write(rows, fieldnames)
            logger.info(
                "  Progress: %d/%d (done=%d skipped=%d far=%d failed=%d)", i + 1, total, done, skipped, far_away, failed
            )

        await asyncio.sleep(NOMINATIM_DELAY_S)

    # Always flush at the end — row modifications from _already_done
    # (clearing bad coords) must be persisted even if all geocodes failed.
    _atomic_write(rows, fieldnames)

    logger.info("Complete: done=%d skipped=%d far=%d failed=%d / %d total", done, skipped, far_away, failed, total)


if __name__ == "__main__":
    asyncio.run(main())
