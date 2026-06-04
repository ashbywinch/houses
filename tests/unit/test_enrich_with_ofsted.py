"""Tests for the Ofsted enrichment logic — catch cases where inspection data gets lost."""

from scripts.enrich_with_ofsted import (
    _best_inspection_year,
    _determine_effective_rating,
    _extract_year,
    _generate_ofsted_cell,
    _grade_score,
)


def test_best_inspection_year_oeif_date():
    """OEIF inspection date should yield the inspection year."""
    row = {
        "Inspection start date of latest OEIF graded inspection": "13/01/2024",
        "Inspection start date": "",
        "Date of latest ungraded inspection": "",
    }
    assert _best_inspection_year(row) == "2024"


def test_best_inspection_year_s5_date():
    """Old S5 inspection date should yield the inspection year when OEIF is absent."""
    row = {
        "Inspection start date of latest OEIF graded inspection": "",
        "Inspection start date": "15/06/2022",
        "Date of latest ungraded inspection": "",
    }
    assert _best_inspection_year(row) == "2022"


def test_best_inspection_year_ungraded_date():
    """Ungraded inspection date should yield the inspection year."""
    row = {
        "Inspection start date of latest OEIF graded inspection": "",
        "Inspection start date": "",
        "Date of latest ungraded inspection": "01/03/2023",
    }
    assert _best_inspection_year(row) == "2023"


def test_best_inspection_year_empty():
    """No inspection dates should yield empty string."""
    row = {
        "Inspection start date of latest OEIF graded inspection": "",
        "Inspection start date": "",
        "Date of latest ungraded inspection": "",
    }
    assert _best_inspection_year(row) == ""


def test_best_inspection_year_null():
    """NULL date values should be treated as empty."""
    row = {
        "Inspection start date of latest OEIF graded inspection": "NULL",
        "Inspection start date": "NULL",
        "Date of latest ungraded inspection": "NULL",
    }
    assert _best_inspection_year(row) == ""


def test_determine_effective_rating_oeif():
    """OEIF-graded schools should get the mapped rating."""
    row = {
        "Latest OEIF overall effectiveness": "2",
        "Latest OEIF quality of education": "2",
        "Latest OEIF behaviour and attitudes": "2",
        "Latest OEIF personal development": "2",
        "Latest OEIF effectiveness of leadership and management": "2",
        "Achievement": "",
        "Curriculum and teaching": "",
        "Leadership and governance": "",
        "Personal development and wellbeing": "",
        "Attendance and behaviour": "",
        "Inclusion": "",
        "Ungraded inspection overall outcome": "",
    }
    rating, highlights = _determine_effective_rating(row)
    assert rating == "Good", f"Expected 'Good', got {rating!r}"


def test_determine_effective_rating_no_data():
    """No data should yield empty rating and highlights."""
    row = {
        "Latest OEIF overall effectiveness": "",
        "Latest OEIF quality of education": "",
        "Latest OEIF behaviour and attitudes": "",
        "Latest OEIF personal development": "",
        "Latest OEIF effectiveness of leadership and management": "",
        "Achievement": "",
        "Curriculum and teaching": "",
        "Leadership and governance": "",
        "Personal development and wellbeing": "",
        "Attendance and behaviour": "",
        "Inclusion": "",
        "Ungraded inspection overall outcome": "",
    }
    rating, highlights = _determine_effective_rating(row)
    assert rating == "", f"Expected empty rating, got {rating!r}"


class TestExtractYear:
    """_extract_year — parse UK-format dates."""

    def test_full_date(self):
        assert _extract_year("13/01/2024") == "2024"

    def test_null_string(self):
        assert _extract_year("NULL") == ""

    def test_empty_string(self):
        assert _extract_year("") == ""

    def test_malformed_date(self):
        assert _extract_year("not-a-date") == ""

    def test_short_date(self):
        assert _extract_year("01/24") == ""


class TestGradeScore:
    """_grade_score — map Ofsted ratings to numeric scores."""

    def test_outstanding_is_4(self):
        assert _grade_score("Outstanding") == 4

    def test_good_is_3(self):
        assert _grade_score("Good") == 3

    def test_requires_improvement_is_2(self):
        assert _grade_score("Requires Improvement") == 2

    def test_inadequate_is_1(self):
        assert _grade_score("Inadequate") == 1

    def test_unknown_is_0(self):
        assert _grade_score("Unknown") == 0


class TestGenerateOfstedCell:
    """_generate_ofsted_cell — builds scannable Ofsted text for a row."""

    def test_oeif_graded(self):
        row = {
            "Latest OEIF overall effectiveness": "2",
            "Latest OEIF quality of education": "2",
            "Latest OEIF behaviour and attitudes": "2",
            "Latest OEIF personal development": "2",
            "Latest OEIF effectiveness of leadership and management": "2",
            "Achievement": "",
            "Curriculum and teaching": "",
            "Leadership and governance": "",
            "Personal development and wellbeing": "",
            "Attendance and behaviour": "",
            "Inclusion": "",
            "Ungraded inspection overall outcome": "",
        }
        result = _generate_ofsted_cell(row)
        assert result == "Good", f"Expected 'Good', got {result!r}"

    def test_oeif_with_brighter_dimension(self):
        """When a dimension grade exceeds the overall, it should be highlighted."""
        row = {
            "Latest OEIF overall effectiveness": "2",
            "Latest OEIF quality of education": "1",  # Outstanding > Good
            "Latest OEIF behaviour and attitudes": "2",
            "Latest OEIF personal development": "2",
            "Latest OEIF effectiveness of leadership and management": "2",
            "Achievement": "",
            "Curriculum and teaching": "",
            "Leadership and governance": "",
            "Personal development and wellbeing": "",
            "Attendance and behaviour": "",
            "Inclusion": "",
            "Ungraded inspection overall outcome": "",
        }
        result = _generate_ofsted_cell(row)
        assert result == "Good, Quality Outstanding"

    def test_s5_inferred_rating(self):
        """Old S5 framework: ratings inferred from dimension grades, weakest flagged."""
        row = {
            "Latest OEIF overall effectiveness": "",
            "Latest OEIF quality of education": "",
            "Latest OEIF behaviour and attitudes": "",
            "Latest OEIF personal development": "",
            "Latest OEIF effectiveness of leadership and management": "",
            "Achievement": "Strong standard",
            "Curriculum and teaching": "Strong standard",
            "Leadership and governance": "Expected standard",
            "Personal development and wellbeing": "Expected standard",
            "Attendance and behaviour": "Strong standard",
            "Inclusion": "",
            "Ungraded inspection overall outcome": "",
        }
        result = _generate_ofsted_cell(row)
        # Average of 4+4+3+3+4 = 18/5 = 3.6 → rounds to 4 → "Outstanding"
        assert result.startswith("Outstanding")
        # Worst dimension is the lowest-scored one
        assert "Expected standard" in result

    def test_ungraded_monitoring_visit(self):
        """Ungraded monitoring visit should extract rating from outcome text."""
        row = {
            "Latest OEIF overall effectiveness": "",
            "Latest OEIF quality of education": "",
            "Latest OEIF behaviour and attitudes": "",
            "Latest OEIF personal development": "",
            "Latest OEIF effectiveness of leadership and management": "",
            "Achievement": "",
            "Curriculum and teaching": "",
            "Leadership and governance": "",
            "Personal development and wellbeing": "",
            "Attendance and behaviour": "",
            "Inclusion": "",
            "Ungraded inspection overall outcome": "School remains Good",
        }
        result = _generate_ofsted_cell(row)
        assert result == "Good", f"Expected 'Good', got {result!r}"

    def test_no_data(self):
        row = {
            "Latest OEIF overall effectiveness": "",
            "Latest OEIF quality of education": "",
            "Latest OEIF behaviour and attitudes": "",
            "Latest OEIF personal development": "",
            "Latest OEIF effectiveness of leadership and management": "",
            "Achievement": "",
            "Curriculum and teaching": "",
            "Leadership and governance": "",
            "Personal development and wellbeing": "",
            "Attendance and behaviour": "",
            "Inclusion": "",
            "Ungraded inspection overall outcome": "",
        }
        assert _generate_ofsted_cell(row) == ""

    def test_send_strong_included(self):
        """Notably good SEND provision should be flagged."""
        row = {
            "Latest OEIF overall effectiveness": "2",
            "Latest OEIF quality of education": "2",
            "Latest OEIF behaviour and attitudes": "2",
            "Latest OEIF personal development": "2",
            "Latest OEIF effectiveness of leadership and management": "2",
            "Achievement": "",
            "Curriculum and teaching": "",
            "Leadership and governance": "",
            "Personal development and wellbeing": "",
            "Attendance and behaviour": "",
            "Inclusion": "Strong standard",
            "Ungraded inspection overall outcome": "",
        }
        result = _generate_ofsted_cell(row)
        assert "SEND strong" in result
