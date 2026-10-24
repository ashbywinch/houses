"""Download and filter NR DTD fares data into a compact lookup CSV.

Usage:
    uv run python scripts/download_rail_fares.py

Requires:
    - NATIONAL_RAIL_API_PASSWORD env var set (your NRDP password)
    - NRDP account subscribed to "Fares, Routeing Guide and Timetable data"

This downloads the full fares zip (~200MB), extracts only fares involving
stations near our two destinations (Victoria for Simon, Fenchurch Street
for Lorena), and saves a compact data/rail_fares.csv.
"""

import csv
import io
import logging
import os
import sys
import zipfile
from pathlib import Path
from typing import TypedDict

import httpx
from money import Money

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("download_rail_fares")


class _AuthForm(TypedDict):
    """The NRDP authenticate POST form — the external API's contract."""

    username: str
    password: str


class _FareRow(TypedDict):
    """One row of the filtered fares CSV: the CSV boundary's shape."""

    origin_crs: str
    dest_crs: str
    single_fare_gbp: str


class _NrdtdFares:
    """The NR DTD fares feed: auth/fares endpoints and the destinations we keep."""

    AUTH_URL = "https://opendata.nationalrail.co.uk/authenticate"
    FARES_URL = "https://opendata.nationalrail.co.uk/api/staticfeeds/2.0/fares"
    USERNAME = "ashby@juggler.net"

    # Destination stations we care about
    DEST_CRS = {"VIC", "FST"}
    MIN_FARE_LINE_LENGTH = 20


def _pence_to_money(pence: int) -> Money:
    """NR DTD fares list pence; Money's ``amount`` is the main unit (GBP pounds).

    The pence-per-pound scaling lives here alone — the money layer owns the
    conversion, business code never sees a /100 factor.
    """
    # lucidlint: ignore magic-number the pence-per-pound factor is the money layer's one job (Money owns unit math)
    return Money(str(pence / 100), "GBP")




def authenticate(password: str) -> str:
    resp = httpx.post(
        _NrdtdFares.AUTH_URL,
        data=_AuthForm(username=_NrdtdFares.USERNAME, password=password),
    )
    resp.raise_for_status()
    body = resp.json()
    token = body.get("token")
    if not token:
        logger.error("Auth response missing token: %s", body)
        sys.exit(1)
    logger.info("Authenticated as %s", body.get("username"))
    return token


def download_fares(token: str) -> bytes:
    logger.info("Downloading fares from %s ...", _NrdtdFares.FARES_URL)
    resp = httpx.get(_NrdtdFares.FARES_URL, headers={"X-Auth-Token": token}, timeout=300)
    logger.info("Downloaded %d bytes", len(resp.content))
    return resp.content


def extract_relevant_fares(zip_data: bytes) -> list[_FareRow]:
    """Unzip the fares file and extract only fares involving DEST_CRS stations."""
    relevant: list[_FareRow] = []
    seen = set()
    with zipfile.ZipFile(io.BytesIO(zip_data)) as z:
        for name in z.namelist():
            if not name.startswith("fares/"):
                continue
            with z.open(name) as f:
                text = f.read().decode("latin-1")
                if not any(crs in text for crs in _NrdtdFares.DEST_CRS):
                    continue
                for line in text.splitlines():
                    if not line.strip():
                        continue
                    # Fixed-format: origin_crs(4) dest_crs(4) ... fare(8)
                    # Fare file format: positions 0-3 origin, 4-7 dest, rest is fare data
                    if len(line) < _NrdtdFares.MIN_FARE_LINE_LENGTH:
                        continue
                    origin = line[0:4].strip()
                    dest = line[4:8].strip()
                    if origin in _NrdtdFares.DEST_CRS or dest in _NrdtdFares.DEST_CRS:
                        fare_str = line[16:24].strip()
                        try:
                            fare = _pence_to_money(int(fare_str))
                        except ValueError:
                            continue
                        key = (origin, dest)
                        if key not in seen:
                            seen.add(key)
                            relevant.append(
                                _FareRow(
                                    origin_crs=origin,
                                    dest_crs=dest,
                                    single_fare_gbp=str(fare.amount),
                                )
                            )
    logger.info("Extracted %d relevant fare records", len(relevant))
    return relevant


def main():
    password = os.environ.get("NATIONAL_RAIL_API_PASSWORD")
    if not password:
        logger.error(
            "NATIONAL_RAIL_API_PASSWORD environment variable not set.\n"
            "Set it to your NRDP password and try again:\n"
            "  export NATIONAL_RAIL_API_PASSWORD=your_password"
        )
        sys.exit(1)

    token = authenticate(password)
    zip_data = download_fares(token)
    fares = extract_relevant_fares(zip_data)

    if not fares:
        logger.warning("No relevant fares found — destinations not in fare data?")
        return

    output = Path("data/rail_fares.csv")
    with output.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["origin_crs", "dest_crs", "single_fare_gbp"])
        writer.writeheader()
        writer.writerows(fares)

    logger.info("Wrote %d fares to %s", len(fares), output)
    print(f"\nDone! {len(fares)} fares saved to {output}")


if __name__ == "__main__":
    main()
