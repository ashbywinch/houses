"""Extract Band D council tax rates from the government's ODS dataset.

Downloads the latest "Band D Council Tax figures 1993 onwards" ODS file
from GOV.UK and writes a CSV mapping local authority name → Band D rate
for the latest available year.

Usage:
    uv run python scripts/extract_council_tax_rates.py

Output:
    data/council_tax_rates.csv  —  authority,band_d_rate
"""

from __future__ import annotations

import csv
import logging
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx

logger = logging.getLogger(__name__)

ODS_URL = (
    "https://assets.publishing.service.gov.uk/media/"
    "69e8ab2d9ca985145673b826/Band_D_2026-27.ods"
)
OUTPUT_PATH = Path("data/council_tax_rates.csv")

# Billing authority classes that set council tax for individual properties
BILLING_CLASSES = {"SD", "UA", "MD", "LB"}

NS = {
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
}


def _cell_text(cell) -> str:
    ps = cell.findall(".//text:p", NS)
    return "".join(p.text or "" for p in ps)


def download_ods(url: str, dest: Path) -> None:
    logger.info("Downloading %s", url)
    resp = httpx.get(url, follow_redirects=True, timeout=60)
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    logger.info("Saved %s (%.1f MB)", dest, len(resp.content) / 1e6)


def extract_rates(ods_path: Path) -> list[dict[str, str]]:
    with zipfile.ZipFile(ods_path) as z, z.open("content.xml") as f:
        tree = ET.parse(f)

    tables = tree.getroot().findall(".//table:table", NS)
    # Table 8 (index 8) is "Table 5: Band D area council tax for local authorities"
    # This table has the TOTAL bill (including county, police, fire precepts),
    # unlike Table 1 which only has individual precept portions.
    table = tables[8]
    rows = table.findall(".//table:table-row", NS)

    # Row 2 is the header row — find the latest year column index
    header_cells = rows[2].findall(".//table:table-cell", NS)
    headers = [_cell_text(c) for c in header_cells]

    # Find the "2026 to 2027" column (or latest year)
    year_col = None
    for i, h in enumerate(headers):
        if re.match(r"20\d{2} to 20\d{2}", h):
            year_col = i
    if year_col is None:
        logger.error("Could not find year column in headers")
        return []

    latest_year = headers[year_col]
    logger.info("Latest year column: %s (col %d)", latest_year, year_col)

    rates: list[dict[str, str]] = []
    for row in rows[3:]:  # Skip header rows
        cells = row.findall(".//table:table-cell", NS)
        if len(cells) < year_col + 1:
            continue

        code = _cell_text(cells[0])
        auth = _cell_text(cells[2])
        current = _cell_text(cells[3])
        cls = _cell_text(cells[4])
        rate_raw = _cell_text(cells[year_col])

        # Skip non-current or non-billing authorities
        if current != "YES":
            continue
        if cls not in BILLING_CLASSES:
            continue
        if not auth or not code:
            continue

        rate = _fmt_rate(rate_raw)

        rates.append({"code": code, "authority": auth, "class": cls, "band_d_rate": rate})

    return rates


def _fmt_rate(raw: str) -> str:
    if not raw or raw in ("[z]", ""):
        return ""
    try:
        return f"{float(raw.replace(',', '')):.2f}"
    except ValueError:
        return ""


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    ods_path = Path("/tmp/band_d_live.ods")
    download_ods(ODS_URL, ods_path)
    rates = extract_rates(ods_path)

    if not rates:
        logger.error("No rates extracted")
        sys.exit(1)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["code", "authority", "class", "band_d_rate"])
        writer.writeheader()
        writer.writerows(rates)

    with_rates = sum(1 for r in rates if r["band_d_rate"])
    logger.info("Wrote %d authorities to %s (%d with rates)", len(rates), OUTPUT_PATH, with_rates)


if __name__ == "__main__":
    main()
