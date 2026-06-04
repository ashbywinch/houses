"""Tests for the Ofsted enrichment logic — catch cases where inspection data gets lost."""

from scripts.enrich_with_ofsted import _best_inspection_year, _determine_effective_rating


def test_best_inspection_year_oeif_date():
    """OEIF inspection date should yield the inspection year."""
    row = {"Inspection start date of latest OEIF graded inspection": "13/01/2024",
           "Inspection start date": "", "Date of latest ungraded inspection": ""}
    assert _best_inspection_year(row) == "2024"


def test_best_inspection_year_s5_date():
    """Old S5 inspection date should yield the inspection year when OEIF is absent."""
    row = {"Inspection start date of latest OEIF graded inspection": "",
           "Inspection start date": "15/06/2022",
           "Date of latest ungraded inspection": ""}
    assert _best_inspection_year(row) == "2022"


def test_best_inspection_year_ungraded_date():
    """Ungraded inspection date should yield the inspection year."""
    row = {"Inspection start date of latest OEIF graded inspection": "",
           "Inspection start date": "",
           "Date of latest ungraded inspection": "01/03/2023"}
    assert _best_inspection_year(row) == "2023"


def test_best_inspection_year_empty():
    """No inspection dates should yield empty string."""
    row = {"Inspection start date of latest OEIF graded inspection": "",
           "Inspection start date": "",
           "Date of latest ungraded inspection": ""}
    assert _best_inspection_year(row) == ""


def test_best_inspection_year_null():
    """NULL date values should be treated as empty."""
    row = {"Inspection start date of latest OEIF graded inspection": "NULL",
           "Inspection start date": "NULL",
           "Date of latest ungraded inspection": "NULL"}
    assert _best_inspection_year(row) == ""


def test_determine_effective_rating_oeif():
    """OEIF-graded schools should get the mapped rating."""
    row = {"Latest OEIF overall effectiveness": "2",
           "Latest OEIF quality of education": "2",
           "Latest OEIF behaviour and attitudes": "2",
           "Latest OEIF personal development": "2",
           "Latest OEIF effectiveness of leadership and management": "2",
           "Achievement": "", "Curriculum and teaching": "",
           "Leadership and governance": "", "Personal development and wellbeing": "",
           "Attendance and behaviour": "", "Inclusion": "",
           "Ungraded inspection overall outcome": ""}
    rating, highlights = _determine_effective_rating(row)
    assert rating == "Good", f"Expected 'Good', got {rating!r}"


def test_determine_effective_rating_no_data():
    """No data should yield empty rating and highlights."""
    row = {"Latest OEIF overall effectiveness": "",
           "Latest OEIF quality of education": "", "Latest OEIF behaviour and attitudes": "",
           "Latest OEIF personal development": "",
           "Latest OEIF effectiveness of leadership and management": "",
           "Achievement": "", "Curriculum and teaching": "",
           "Leadership and governance": "", "Personal development and wellbeing": "",
           "Attendance and behaviour": "", "Inclusion": "",
           "Ungraded inspection overall outcome": ""}
    rating, highlights = _determine_effective_rating(row)
    assert rating == "", f"Expected empty rating, got {rating!r}"
