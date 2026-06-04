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



def _grade_score(grade: str) -> int:
    return {"Outstanding": 4, "Good": 3, "Requires Improvement": 2, "Inadequate": 1}.get(grade, 0)


def _s5_score(grade: str) -> int:
    return {"Strong standard": 4, "Expected standard": 3, "Needs attention": 2, "Cause for concern": 1}.get(grade, 0)


def _determine_effective_rating(row: dict) -> tuple[str, str]:
    """Determine the most accurate rating and a highlights string.

    Returns (rating, highlights) where rating is the best estimate of the
    school's quality and highlights captures anything notable worth mentioning.

    Handles:
    - OEIF-graded schools (overall grade + dimension highlights)
    - Old-format S5 inspections (dimension grades + worst area flag)
    - Ungraded monitoring visits (rating only, no year/framework noise)
    - "Not judged" or missing overall grade → infer from dimension grades
    """
    oeif_dims = [
        ("Quality", "Latest OEIF quality of education"),
        ("Behaviour", "Latest OEIF behaviour and attitudes"),
        ("Personal development", "Latest OEIF personal development"),
        ("Leadership", "Latest OEIF effectiveness of leadership and management"),
    ]
    s5_dims = [
        ("Achievement", "Achievement"),
        ("Curriculum", "Curriculum and teaching"),
        ("Leadership", "Leadership and governance"),
        ("Personal development", "Personal development and wellbeing"),
        ("Attendance", "Attendance and behaviour"),
    ]

    oeif_raw = (row.get("Latest OEIF overall effectiveness") or "").strip()
    oeif_rating = OEIF_RATING_MAP.get(oeif_raw, "") if oeif_raw and oeif_raw != "NULL" else ""

    # Collect OEIF dimension grades
    oeif_grades: list[tuple[str, str]] = []
    for label, col in oeif_dims:
        val = (row.get(col) or "").strip()
        if val and val != "NULL":
            oeif_grades.append((label, OEIF_RATING_MAP.get(val, val)))

    # If we have OEIF dimension grades but no overall rating, infer from dimensions
    if not oeif_rating and oeif_grades:
        scores = [_grade_score(g) for _, g in oeif_grades]
        avg = sum(scores) / len(scores) if scores else 0
        oeif_rating = {4: "Outstanding", 3: "Good", 2: "Requires Improvement", 1: "Inadequate"}.get(round(avg), "")

    if oeif_rating:
        highlights = []
        for label, grade in oeif_grades:
            if _grade_score(grade) > _grade_score(oeif_rating):
                highlights.append(f"{label} {grade}")
        # S5 data available as supplement
        s5_data = {label: (row.get(col) or "").strip() for label, col in s5_dims}
        s5_data = {k: v for k, v in s5_data.items() if v and v != "NULL"}
        if not highlights and s5_data:
            worst = min(s5_data, key=lambda k: _s5_score(s5_data[k]))
            highlights.append(f"{worst} {s5_data[worst]}")
        highlights_str = ", ".join(highlights) if highlights else ""
        return oeif_rating, highlights_str

    # Old-format S5 inspection
    s5_data = {label: (row.get(col) or "").strip() for label, col in s5_dims}
    s5_data = {k: v for k, v in s5_data.items() if v and v != "NULL"}
    if s5_data:
        scores = [_s5_score(v) for v in s5_data.values()]
        avg = sum(scores) / len(scores) if scores else 0
        rating = {4: "Outstanding", 3: "Good", 2: "Requires Improvement", 1: "Inadequate"}.get(round(avg), "")
        worst = min(s5_data, key=lambda k: _s5_score(s5_data[k]))
        return rating, f"{worst} {s5_data[worst]}"

    # Ungraded monitoring visit — just the rating, that's all we know
    ungraded = (row.get("Ungraded inspection overall outcome") or "").strip()
    if ungraded and ungraded != "NULL":
        for phrase, grade in _UNGRADED_GRADE_MAP.items():
            if phrase in ungraded.lower():
                return grade, ""

    return "", ""


def _generate_ofsted_cell(row: dict) -> str:
    """Build a single-column Ofsted rating that's scannable and jargon-free.

    Format examples:
        "Good"                              — plain rating, nothing else notable
        "Good, Behaviour Outstanding"        — one dimension stands out
        "Outstanding"                        — clean
        "Good, Attendance needs attention"   — S5 school with a weak area
        "Good, SEND strong"                 — Inclusion is notably good
        ""                                  — no data available

    Rules:
    - No "OEIF", "S5", "Ofsted" jargon
    - No year (it's in its own column)
    - No pipe symbols
    - Capitalised labels: "Behaviour" not "behaviour"
    - "Not judged" is avoided: if dimension grades exist, use their consensus
    """
    rating, highlights = _determine_effective_rating(row)

    if not rating:
        return ""

    parts = [rating]
    if highlights:
        parts.append(highlights)

    # SEND: mention if notably good or bad
    inclusion = (row.get("Inclusion") or "").strip()
    if inclusion and inclusion != "NULL" and inclusion not in ("Expected standard", ""):
        send_flags = {
            "Strong standard": "SEND strong",
            "Exceptional": "SEND exceptional",
            "Needs attention": "SEND needs attention",
            "Urgent improvement": "SEND urgent improvement",
        }
        flag = send_flags.get(inclusion)
        if flag:
            parts.append(flag)

    return ", ".join(parts)


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
            rating = _determine_effective_rating(ofsted_row)[0]
            year = _best_inspection_year(ofsted_row)
            summary = _generate_ofsted_cell(ofsted_row)

            school["OfstedRating (name)"] = summary
            school["InspectionYear"] = year  # separate column for formulas to XLOOKUP
            school["InspectionSummary"] = ""  # merged into OfstedRating
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
