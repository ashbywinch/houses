"""One-time script: enrich GIAS CSV with lat/lng from postcodes.io bulk API.

Usage:
    uv run python scripts/enrich_schools.py

Reads edubaseallstatefunded*.csv, geocodes all unique school postcodes
in batches of 100, writes edubaseall_enriched.csv with Latitude/Longitude columns.
"""

import csv
import sys
import time
from pathlib import Path

import httpx

DATA_DIR = Path("data")
POSTCODES_IO_BULK = "https://api.postcodes.io/postcodes"
HTTP_OK = 200
RETRY_DELAY_SECONDS = 0.1
POLITE_DELAY_SECONDS = 0.5

# Find the state-funded CSV
csv_files = sorted(DATA_DIR.glob("edubaseallstatefunded*.csv"))
if not csv_files:
    print("No state-funded school CSV found in data/")
    sys.exit(1)

src_path = csv_files[-1]  # most recent
dst_path = DATA_DIR / "edubaseall_enriched.csv"

print(f"Reading: {src_path}")
print(f"Output:  {dst_path}")

# Read all rows
with src_path.open(newline="", encoding="latin-1") as f:
    reader = csv.DictReader(f)
    rows = list(reader)

print(f"Loaded {len(rows)} rows")

# Collect unique non-empty postcodes
postcodes = sorted({r["Postcode"].strip().upper() for r in rows if r.get("Postcode", "").strip()})
print(f"Unique postcodes to geocode: {len(postcodes)}")

# Geocode in batches of 100
# lucidlint: ignore global-state bounded module cache/state — single writer, deliberate
results: dict[str, tuple[float, float]] = {}
batch_size = 100


def _geocode_batch(client, batch, results, offset, total) -> bool:
    """Bulk-geocode one batch of postcodes; True when the batch call succeeded."""
    try:
        resp = client.post(
            POSTCODES_IO_BULK,
            json={"postcodes": batch},
        )
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("result", []):
            if item and item.get("result"):
                pc = item["query"].upper()
                results[pc] = (item["result"]["latitude"], item["result"]["longitude"])
        print(f"  [{offset}/{total}] geocoded {len(results)} so far")
        return True
    # lucidlint: ignore broad-except deliberate fallback — any batch failure falls back to per-postcode lookups
    except Exception as e:
        print(f"  Batch failed at {offset}: {e}")
        return False


def _geocode_individually(client, batch, results) -> None:
    """Fallback for a failed batch: look up each postcode individually."""
    for pc in batch:
        try:
            r2 = client.get(f"{POSTCODES_IO_BULK}/{pc}")
            if r2.status_code == HTTP_OK:
                data2 = r2.json()
                if data2.get("result"):
                    results[pc] = (data2["result"]["latitude"], data2["result"]["longitude"])
        # lucidlint: ignore broad-except deliberate per-item resilience — one failed postcode continues the batch
        except Exception as e:
            print(f"  Individual lookup failed for {pc}: {e}")
            time.sleep(RETRY_DELAY_SECONDS)
            continue
        time.sleep(RETRY_DELAY_SECONDS)


with httpx.Client(timeout=30.0) as client:
    for i in range(0, len(postcodes), batch_size):
        batch = postcodes[i : i + batch_size]
        if not _geocode_batch(client, batch, results, i, len(postcodes)):
            _geocode_individually(client, batch, results)
        time.sleep(POLITE_DELAY_SECONDS)  # be respectful

print(f"Successfully geocoded: {len(results)} / {len(postcodes)}")

# Add lat/lng columns to rows
fieldnames = list(rows[0].keys()) + ["Latitude", "Longitude"]
with dst_path.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        pc = row.get("Postcode", "").strip().upper()
        if pc in results:
            row["Latitude"] = str(results[pc][0])
            row["Longitude"] = str(results[pc][1])
        else:
            row["Latitude"] = ""
            row["Longitude"] = ""
        writer.writerow(row)

print(f"Written: {dst_path}")
print("Done! Now update SCHOOLS_CSV_PATH in enricher.py to point to the enriched file.")
