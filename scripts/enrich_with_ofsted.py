"""Merge Ofsted data into the enriched school CSV.

Adds:
- OfstedRating (name) — existing: OEIF grade, falling back to ungraded outcome
- InspectionYear — year of the most relevant inspection
- InspectionSummary — concise textual summary of key findings

Usage: uv run python scripts/enrich_with_ofsted.py
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

ENRICHED_CSV = Path("data/edubaseall_enriched.csv")
OFSTED_CSV = Path("data/ofsted_inspections.csv")

OEIF_RATING_MAP = {
    "1": "Outstanding",
    "2": "Good",
    "3": "Requires Improvement",
    "4": "Inadequate",
}

# Ungraded outcomes often contain the school's current rating
_GRADE_FROM_UNGRADED = re.compile(
    r"(Outstanding|Good|Requires Improvement|Inadequate|Special Measures)",
    re.IGNORECASE,
)

# Map ungraded outcome phrases to implied grades
_UNGRADED_GRADE_MAP: dict[str, str] = {
    "some aspects not as strong": "Requires Improvement",
    "concerns": "Requires Improvement",
    "special measures": "Inadequate",
    "school remains good": "Good",
    "school remains outstanding": "Outstanding",
    "standards maintained": "Good",
    "met": "Good",
    "not met": "Inadequate",
    "serious weaknesses": "Inadequate",
    "insufficient progress": "Requires Improvement",
    "reasonable progress": "Requires Improvement",
    "significant improvement": "Good",
}


def _extract_year(date_str: str) -> str:
    """Extract year from a UK-format date string like '13/01/2026'."""
    if not date_str or date_str == "NULL":
        return ""
    parts = date_str.split("/")
    if len(parts) == 3:
        return parts[2]
    return ""


def _best_inspection_year(row: dict) -> str:
    """Find the most relevant inspection year from available date fields."""
    # Priority: OEIF graded inspection > old full inspection > ungraded
    for date_field in [
        "Inspection start date of latest OEIF graded inspection",
        "Inspection start date",
        "Date of latest ungraded inspection",
    ]:
        val = row.get(date_field, "")
        if val and val != "NULL":
            year = _extract_year(val)
            if year:
                return year
    return ""


def _determine_rating(row: dict) -> str:
    """Determine the best rating for a school from all available data.

    Priority:
    1. OEIF overall effectiveness
    2. Old-format S5 inspection grade dimensions
    3. Ungraded inspection outcome text
    """
    urn = row.get("URN", "")

    # Priority 1: OEIF
    oeif = (row.get("Latest OEIF overall effectiveness") or "").strip()
    if oeif and oeif != "NULL":
        return OEIF_RATING_MAP.get(oeif, oeif)

    # Priority 2: Old-format S5 with grade dimensions
    # Summarise the pattern of grades across dimensions
    dims = []
    for dim in ["Achievement", "Curriculum and teaching", "Leadership and governance",
                 "Personal development and wellbeing", "Attendance and behaviour"]:
        val = (row.get(dim) or "").strip()
        if val and val != "NULL":
            dims.append(val)

    if dims:
        # Map old framework descriptors to OEIF-like grades
        if all(d == "Strong standard" for d in dims):
            return "Outstanding"
        if all(d in ("Strong standard", "Expected standard") for d in dims):
            return "Good"
        if any(d == "Needs attention" for d in dims):
            if any(d == "Cause for concern" for d in dims):
                return "Inadequate"
            return "Requires Improvement"
        # Fall back to majority
        return max(set(dims), key=dims.count)

    # Priority 3: Ungraded inspection outcome
    ungraded = (row.get("Ungraded inspection overall outcome") or "").strip()
    if ungraded and ungraded != "NULL":
        match = _GRADE_FROM_UNGRADED.search(ungraded)
        if match:
            return match.group(1)
        # Try phrase map for descriptive outcomes
        lower = ungraded.lower()
        for phrase, grade in _UNGRADED_GRADE_MAP.items():
            if phrase in lower:
                return grade

    return ""


def _generate_summary(row: dict, rating: str) -> str:
    """Generate a concise summary: rating + source + SEND flag if notable."""
    parts = []

    # Source: OEIF > old S5 > ungraded
    oeif = (row.get("Latest OEIF overall effectiveness") or "").strip()
    oeif_date = row.get("Inspection start date of latest OEIF graded inspection", "")
    if oeif and oeif != "NULL":
        year = _extract_year(oeif_date)
        source = f"OEIF {year}" if year else "OEIF"
        parts.append(f"{rating} ({source})")
    else:
        s5_dims = [
            (row.get(d) or "").strip()
            for d in ["Achievement", "Curriculum and teaching", "Leadership and governance",
                       "Personal development and wellbeing", "Attendance and behaviour"]
        ]
        s5_dims = [d for d in s5_dims if d and d != "NULL"]
        insp_date = row.get("Inspection start date", "")
        if s5_dims:
            year = _extract_year(insp_date)
            s5_type = (row.get("Inspection type") or "").strip()
            source = f"{s5_type} {year}" if year else (s5_type or "S5")
            parts.append(f"{rating} ({source})")
        else:
            ungraded = (row.get("Ungraded inspection overall outcome") or "").strip()
            ungraded_date = row.get("Date of latest ungraded inspection", "")
            if ungraded and ungraded != "NULL":
                year = _extract_year(ungraded_date)
                source = f"ungraded {year}" if year else "ungraded"
                label = f"{rating} " if rating else ""
                parts.append(f"{label}({source}: {ungraded})")

    # SEND: only mention if notably good (Strong/Exceptional) or bad (Needs attention/Urgent)
    inclusion = (row.get("Inclusion") or "").strip()
    if inclusion and inclusion != "NULL" and inclusion not in ("Expected standard", ""):
        send_label = {
            "Strong standard": "SEND: Strong",
            "Exceptional": "SEND: Exceptional",
            "Needs attention": "SEND: Needs attention",
            "Urgent improvement": "SEND: Urgent improvement",
        }.get(inclusion, f"SEND: {inclusion}")
        parts.append(send_label)

    return " | ".join(parts) if parts else rating


def main():
    if not ENRICHED_CSV.is_file():
        print(f"ERROR: {ENRICHED_CSV} not found", file=sys.stderr)
        sys.exit(1)
    if not OFSTED_CSV.is_file():
        print(f"ERROR: {OFSTED_CSV} not found", file=sys.stderr)
        sys.exit(1)

    # Load all Ofsted rows into a dict by URN
    ofsted_by_urn: dict[str, dict] = {}
    with OFSTED_CSV.open(newline="", encoding="latin-1") as f:
        reader = csv.DictReader(f)
        for row in reader:
            urn = row.get("URN", "").strip()
            if urn:
                ofsted_by_urn[urn] = row

    print(f"Loaded {len(ofsted_by_urn)} Ofsted records")

    with ENRICHED_CSV.open(newline="", encoding="latin-1") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        schools = list(reader)

    # Add new columns if not present
    new_cols = {
        "OfstedRating (name)",
        "InspectionYear",
        "InspectionSummary",
    }
    existing = set(fieldnames)
    for col in sorted(new_cols - existing):
        fieldnames.append(col)

    matched = 0
    for school in schools:
        urn = school.get("URN", "").strip()
        ofsted_row = ofsted_by_urn.get(urn)

        if ofsted_row:
            rating = _determine_rating(ofsted_row)
            year = _best_inspection_year(ofsted_row)
            summary = _generate_summary(ofsted_row, rating)

            school["OfstedRating (name)"] = rating
            school["InspectionYear"] = year
            school["InspectionSummary"] = summary
            if rating:
                matched += 1
        else:
            school.setdefault("OfstedRating (name)", "")
            school.setdefault("InspectionYear", "")
            school.setdefault("InspectionSummary", "")

    with ENRICHED_CSV.open("w", newline="", encoding="latin-1") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(schools)

    print(f"Wrote {len(schools)} rows to {ENRICHED_CSV}")
    print(f"Matched {matched}/{len(schools)} schools with Ofsted data")

    # Show sample
    rating_filled = sum(1 for s in schools if s.get("OfstedRating (name)", "").strip())
    year_filled = sum(1 for s in schools if s.get("InspectionYear", "").strip())
    print(f"OfstedRating filled: {rating_filled}/{len(schools)}")
    print(f"InspectionYear filled: {year_filled}/{len(schools)}")


if __name__ == "__main__":
    main()
