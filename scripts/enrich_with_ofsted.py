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
    """Build a single-column Ofsted rating that combines rating, year, and notable findings.

    Format: "Good (OEIF 2024) | behaviour Outstanding, personal dev Outstanding"
    This replaces the separate Ofsted + Year + Summary columns with one scannable value.
    """
    parts = []

    OEIF_DIMS = [
        ("quality", "Latest OEIF quality of education"),
        ("behaviour", "Latest OEIF behaviour and attitudes"),
        ("personal dev", "Latest OEIF personal development"),
        ("leadership", "Latest OEIF effectiveness of leadership and management"),
    ]
    S5_DIMS = [
        ("Achievement", "Achievement"),
        ("Curriculum", "Curriculum and teaching"),
        ("Leadership", "Leadership and governance"),
        ("Personal dev", "Personal development and wellbeing"),
        ("Attendance", "Attendance and behaviour"),
    ]

    oeif = (row.get("Latest OEIF overall effectiveness") or "").strip()

    if oeif and oeif != "NULL":
        oeif_date = row.get("Inspection start date of latest OEIF graded inspection", "")
        year = _extract_year(oeif_date)
        tag = f"OEIF {year}" if year else "OEIF"

        # Collect dimension grades that differ from overall
        better = []
        for label, col in OEIF_DIMS:
            val = (row.get(col) or "").strip()
            if val and val != "NULL":
                mapped = OEIF_RATING_MAP.get(val, val)
                if _grade_score(mapped) > _grade_score(rating):
                    better.append(f"{label} {mapped}")

        if better:
            parts.append(f"{rating} ({tag}) | {', '.join(better)}")
        else:
            parts.append(f"{rating} ({tag})")

    else:
        s5_data = {label: (row.get(col) or "").strip()
                   for label, col in S5_DIMS}
        s5_data = {k: v for k, v in s5_data.items() if v and v != "NULL"}

        insp_date = row.get("Inspection start date", "")
        if s5_data:
            year = _extract_year(insp_date)
            s5_type = (row.get("Inspection type") or "").strip()
            tag = f"{s5_type} {year}" if year else (s5_type or "S5")
            worst_label = min(s5_data, key=lambda k: _s5_score(s5_data[k]))
            worst_val = s5_data[worst_label]
            parts.append(f"{rating} ({tag}) | {worst_label} {worst_val}")
        else:
            ungraded = (row.get("Ungraded inspection overall outcome") or "").strip()
            ungraded_date = row.get("Date of latest ungraded inspection", "")
            if ungraded and ungraded != "NULL":
                year = _extract_year(ungraded_date)
                tag = f"monitoring visit {year}" if year else "monitoring visit"
                # The outcome text repeats the rating ("School remains Good").
                # Just the year and tag are enough — the rating column already has the grade.
                parts.append(f"{rating} ({tag})" if rating else f"({tag}: {ungraded})")

    # SEND: mention if notably good or bad
    inclusion = (row.get("Inclusion") or "").strip()
    if inclusion and inclusion != "NULL" and inclusion not in ("Expected standard", ""):
        send_label = {
            "Strong standard": "SEND strong",
            "Exceptional": "SEND exceptional",
            "Needs attention": "SEND needs attention",
            "Urgent improvement": "SEND urgent improvement",
        }.get(inclusion, f"SEND: {inclusion}")
        parts.append(send_label)

    return " | ".join(parts) if parts else rating


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
    OEIF_DIMS = [
        ("Quality", "Latest OEIF quality of education"),
        ("Behaviour", "Latest OEIF behaviour and attitudes"),
        ("Personal development", "Latest OEIF personal development"),
        ("Leadership", "Latest OEIF effectiveness of leadership and management"),
    ]
    S5_DIMS = [
        ("Achievement", "Achievement"),
        ("Curriculum", "Curriculum and teaching"),
        ("Leadership", "Leadership and governance"),
        ("Personal development", "Personal development and wellbeing"),
        ("Attendance", "Attendance and behaviour"),
    ]

    oeif_raw = (row.get("Latest OEIF overall effectiveness") or "").strip()
    oeif_rating = OEIF_RATING_MAP.get(oeif_raw, "") if oeif_raw and oeif_raw != "NULL" else ""

    # Collect OEIF dimension grades
    oeif_dims = []
    for label, col in OEIF_DIMS:
        val = (row.get(col) or "").strip()
        if val and val != "NULL":
            oeif_dims.append((label, OEIF_RATING_MAP.get(val, val)))

    # If we have OEIF dimension grades but no overall rating, infer from dimensions
    if not oeif_rating and oeif_dims:
        scores = [_grade_score(g) for _, g in oeif_dims]
        avg = sum(scores) / len(scores) if scores else 0
        oeif_rating = {4: "Outstanding", 3: "Good", 2: "Requires Improvement", 1: "Inadequate"}.get(round(avg), "")

    if oeif_rating:
        highlights = []
        for label, grade in oeif_dims:
            if _grade_score(grade) > _grade_score(oeif_rating):
                highlights.append(f"{label} {grade}")
        # S5 data available as supplement
        s5_data = {label: (row.get(col) or "").strip()
                   for label, col in S5_DIMS}
        s5_data = {k: v for k, v in s5_data.items() if v and v != "NULL"}
        if not highlights and s5_data:
            worst = min(s5_data, key=lambda k: _s5_score(s5_data[k]))
            highlights.append(f"{worst} {s5_data[worst]}")
        highlights_str = ", ".join(highlights) if highlights else ""
        return oeif_rating, highlights_str

    # Old-format S5 inspection
    s5_data = {label: (row.get(col) or "").strip()
               for label, col in S5_DIMS}
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
