"""APCOA parking-rate helpers: tariff text, and the URL slugs its pages use.

Pure functions with no I/O. They live here, in the houses layer, because both
`houses.apcoa_scraper` and `scripts/sync_parking_rates` use them — the script
importing the scraper (or the reverse) closed a cycle.
"""

from __future__ import annotations

import re


def extract_daily_rate_from_tariff(tariff_text: str) -> float | None:
    """Extract the weekday daily parking rate from APCOA tariff text.

    Handles several formats:
    - "Daily Rate: £X.XX" (Woking, Fleet)
    - "Daily Rate before 12pm: £X.XX" (Bourne End)
    - "Up to 24 hours £X.XX" (Didcot Foxhall)
    - Multi-line tariff from the Monday-Friday section.

    Returns:
        float — the daily rate in GBP
        None — if no rate could be confidently extracted

    Designed as a pure function — no I/O, no side effects. Testable
    with fixture files containing ``Parking tariff`` section text.
    """
    # The caller already extracts the relevant section from the page.
    # The text may or may not start with "Parking tariff".
    tariff_start = tariff_text.find("Parking tariff")
    if tariff_start < 0:
        tariff_start = tariff_text.find("Pricing and payment")
    if tariff_start < 0:
        return None
    tariff = tariff_text[tariff_start:]

    # Isolate the Monday-Friday block (ends before Saturday or weeklies).
    # "Sunday" is too broad (matches "Monday - Sunday"), so we only
    # anchor on "Saturday" which cleanly separates weekday from weekend.
    mf_end = len(tariff)
    for marker in ["Saturday", "Weekly\n", "Monthly\n", "Season Ticket"]:
        idx = tariff.find(marker)
        if idx >= 0 and idx < mf_end:
            mf_end = idx
    mf = tariff[:mf_end]

    # Check for "Permit Holders Only" — no daily rate available
    if re.search(r"Permit\s*Holders?\s*Only", mf, re.IGNORECASE):
        return None

    # Strategy 1 then 2: explicit "Daily Rate: £X.XX" (handles "before 12pm"
    # qualifiers), then "Up to 24 hours £X.XX" / "Up to 24 hours: £X.XX".
    for pattern in (
        r"Daily\s*Rate\s*\S*?\s*:?\s*£(\d+\.\d{2})",
        r"(?:Up\s*to\s*)?24\s*hours?\s*:?\s*£(\d+\.\d{2})",
    ):
        m = re.search(pattern, mf, re.IGNORECASE)
        if m:
            return float(m.group(1))

    # Strategy 3: extract all £X.XX prices, take the highest (likely daily max)
    prices = [float(p) for p in re.findall(r"£(\d+\.\d{2})", mf)]
    if prices:
        max_price = max(prices)
        if max_price > 0:
            return max_price

    return None


def make_slug(name: str) -> str:
    """Convert a station name to an APCOA URL slug."""
    slug = name.lower()
    slug = slug.replace("'", "").replace("&", "and")
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def city_slugs(station_name: str) -> list[str]:
    """Generate candidate city slugs for APCOA URL construction.

    Tries the full station name first (e.g. 'Bourne End' →
    'bourne-end'), then falls back to the first word
    (e.g. 'Didcot Parkway' → 'didcot').
    """
    name = station_name.strip()
    full = make_slug(name)
    first = make_slug(name.split()[0])
    candidates = [full]
    if first != full:
        candidates.append(first)
    return candidates
