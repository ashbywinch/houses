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
postcodes = sorted({
    r["Postcode"].strip().upper()
    for r in rows
    if r.get("Postcode", "").strip()
})
print(f"Unique postcodes to geocode: {len(postcodes)}")

# Geocode in batches of 100
results: dict[str, tuple[float, float]] = {}
batch_size = 100

with httpx.Client(timeout=30.0) as client:
    for i in range(0, len(postcodes), batch_size):
        batch = postcodes[i : i + batch_size]
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
            print(f"  [{i}/{len(postcodes)}] geocoded {len(results)} so far")
        except Exception as e:
            print(f"  Batch failed at {i}: {e}")
            # Retry individual lookups for failed batch
            for pc in batch:
                try:
                    r2 = client.get(f"{POSTCODES_IO_BULK}/{pc}")
                    if r2.status_code == 200:
                        data2 = r2.json()
                        if data2.get("result"):
                            results[pc] = (data2["result"]["latitude"], data2["result"]["longitude"])
                except Exception:
                    pass
                time.sleep(0.1)
        time.sleep(0.5)  # be respectful

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
