"""Extract all fares to London Victoria and Fenchurch Street from NR DTD data.

FFL contains RF (flow) records mapping origin→dest to a FLOW_ID, and
RT (ticket) records mapping FLOW_ID + ticket_code to a FARE (in pence).
This extracts the cheapest single-type fare for each origin→VIC/FST route.

Usage: uv run python scripts/extract_rail_fares.py <fares_zip_path>
"""

import csv
import logging
import sys
import zipfile

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("extract_rail_fares")

# London terminal NLCs and their CRS codes
DEST_NLCS = {
    "5426": "VIC", "7490": "FST", "1444": "EUS", "3087": "PAD",
    "5158": "WAE", "5598": "WAT", "6965": "LST", "5148": "LBG",
    "6121": "KGX", "1555": "STP", "5142": "CST", "5143": "CHX",
    "5112": "BFR", "6969": "SRA", "1072": "LON",
}
# Standard walk-up ticket codes. A fare is valid at peak times iff its
# RESTRICTION_CODE (positions 20-21 of RT record) is empty.
# The ticket type name alone doesn't determine peak validity — an SDS (Super
# Off-Peak Single) with no restriction code is valid anytime.
SINGLE_CODES = {"SDS", "SVS", "SOS", "SSS", "ADS", "FDS", "SDR", "SOR", "TKR"}
RETURN_CODES = {"CDR", "SVR", "FOR", "FDR", "OR2", "OR1"}


def main():
    if len(sys.argv) < 2:
        print("Usage: uv run python scripts/extract_rail_fares.py <fares_zip_path>")
        sys.exit(1)

    with zipfile.ZipFile(sys.argv[1]) as z:
        # Build NLC->CRS from LOC
        nlc_crs = {}
        loc_name = [n for n in z.namelist() if ".LOC" in n][0]
        for raw in z.open(loc_name):
            line = raw.decode("latin-1")
            if line.startswith("/") or not line.startswith("RL") or len(line) < 60:
                continue
            nlc = line[36:40].strip()
            crs = line[56:59].strip()
            name = line[40:55].strip()
            if nlc.isdigit() and len(crs) == 3 and crs.isupper():
                nlc_crs[nlc] = (crs, name.strip())
        logger.info("LOC: %d stations", len(nlc_crs))

        # Pass 1: Scan RF records for flows to VIC/FST
        ffl_name = [n for n in z.namelist() if ".FFL" in n][0]
        dest_flows = {}  # flow_id -> (origin_nlc, dest_crs)
        total_rf = 0

        for raw in z.open(ffl_name):
            line = raw.decode("latin-1")
            if line.startswith("/") or len(line) < 30:
                continue
            if not line.startswith("RF"):
                continue
            total_rf += 1
            origin = line[2:6].strip().lstrip("0")
            dest = line[6:10].strip().lstrip("0")
            flow_id = line[42:49].strip().lstrip("0")
            if dest in DEST_NLCS:
                dest_flows[flow_id] = (origin, DEST_NLCS[dest])

        logger.info("FFL: %d RF records, %d flows to destinations", total_rf, len(dest_flows))

        # Pass 2: Scan RT records for fares matching those flows
        total_rt = 0
        fares = {}  # (origin_nlc, dest_crs) -> (single_equiv_price, ticket_code)
        for raw in z.open(ffl_name):
            line = raw.decode("latin-1")
            if line.startswith("/") or len(line) < 22:
                continue
            if not line.startswith("RT"):
                continue
            total_rt += 1
            flow_id = line[2:9].strip().lstrip("0")
            if flow_id not in dest_flows:
                continue
            ticket = line[9:12].strip()
            if ticket not in SINGLE_CODES and ticket not in RETURN_CODES:
                continue
            # Skip tickets with time restrictions — only use unrestricted (peak-valid) fares
            rest = line[20:22].strip()
            if rest:
                continue
            fare_str = line[12:20].strip()
            if not fare_str.isdigit():
                continue
            price = float(fare_str) / 100.0
            origin_nlc, dest_crs = dest_flows[flow_id]
            key = (origin_nlc, dest_crs)

            # Convert return tickets to single-equivalent for comparison
            if ticket in RETURN_CODES:
                price = round(price / 2, 2)

            # Keep cheapest single-equivalent fare
            if key not in fares or price < fares[key][0]:
                fares[key] = (price, ticket)

        logger.info("FFL: %d RT records, %d unique fares extracted", total_rt, len(fares))

        # Write output
        output = "data/rail_fares.csv"
        with open(output, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["origin_crs", "dest_crs", "single_fare_gbp", "ticket_code"])
            for (nlc, dest), (price, ticket) in sorted(fares.items()):
                crs, name = nlc_crs.get(nlc, ("", ""))
                w.writerow([crs, dest, price, ticket])

        logger.info("Wrote %d fares to %s", len(fares), output)
        print(f"\nDone! {len(fares)} fares to {output}")


if __name__ == "__main__":
    main()
