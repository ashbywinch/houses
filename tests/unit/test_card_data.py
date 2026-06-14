"""Tests for card_data — sheet-row to card-model transformation.

Tests ``_build_card`` (pure function, no I/O) with synthetic sheet data,
and ``get_all_cards`` with mocked sheet I/O boundary.
"""

from __future__ import annotations

from unittest import mock

import pytest

from houses.sheets.formulas import VIEW_HEADERS
from houses.sheets.row import Row
from houses.web.card_data import _build_card, get_all_cards, commute_colour, ofsted_colour, walk_colour


# ── Test data helpers ────────────────────────────────────────────────────


def _data(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Build a Data tab row with defaults that produce a well-rounded card."""
    row = {h: "" for h in Row.HEADERS}
    row.update(
        {
            "Rightmove ID": "12345",
            "Rightmove URL": "https://www.rightmove.co.uk/properties/12345",
            "Address": "48 Acacia Avenue, Southall, UB2",
            "Postcode": "UB2 5AD",
            "Bedrooms": "3",
            "Price (£)": "450000",
            "Simon London (min)": "32",
            "Simon London Cost (£)": "4.50",
            "Lorena London (min)": "45",
            "Lorena London Cost (£)": "2.80",
            "Bracknell Time (min)": "22",
            "Bracknell Cost (£)": "6.00",
            "Primary School": "St Mary's Primary",
            "Primary Walk (min)": "4",
            "Primary Ofsted": "Outstanding",
            "Primary Inspection Year": "2023",
            "Primary School Link": "https://get-information-schools.service.gov.uk/Establishments/Establishment/Details/100",
            "Secondary School": "The Academy",
            "Secondary Walk (min)": "12",
            "Secondary Ofsted": "Good",
            "Secondary Inspection Year": "2022",
            "Secondary School Link": "https://get-information-schools.service.gov.uk/Establishments/Establishment/Details/200",
            "Walk to Town (min)": "10",
            "Best Latitude": "51.5",
            "Best Longitude": "-0.4",
            "Map URL": "https://www.google.com/maps?q=51.5,-0.4",
        }
    )
    if overrides:
        row.update(overrides)
    return row


def _view(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Build a View tab row with defaults."""
    row = {h: "" for h in VIEW_HEADERS}
    row.update(
        {
            "Rightmove ID": "12345",
            "Total Monthly Housing Cost (£)": "2850.00",
            "Status": "Maybe",
        }
    )
    if overrides:
        row.update(overrides)
    return row


# ── Column header validation ─────────────────────────────────────────────


class TestColumnHeaders:
    """Test data column sets must stay in sync with production constants."""

    def test_data_defaults_cover_all_columns(self):
        row = {h: "" for h in Row.HEADERS}
        row.update({"Rightmove ID": "x"})
        card = _build_card(row, {"Rightmove ID": "x"})
        assert card.rid == "x"

    def test_view_defaults_cover_all_columns(self):
        row = {h: "" for h in VIEW_HEADERS}
        row.update({"Rightmove ID": "x", "Status": "Maybe"})
        card = _build_card({"Rightmove ID": "x"}, row)
        assert card.status == "Maybe"


# ── Colour helpers ───────────────────────────────────────────────────────


class TestCommuteColour:
    def test_simon_good(self):
        assert commute_colour(30, bracknell=False) == "good"

    def test_simon_warn(self):
        assert commute_colour(45, bracknell=False) == "warn"

    def test_simon_bad(self):
        assert commute_colour(80, bracknell=False) == "bad"

    def test_boundary_good_warn(self):
        assert commute_colour(44, bracknell=False) == "good"
        assert commute_colour(45, bracknell=False) == "warn"

    def test_boundary_warn_bad(self):
        assert commute_colour(75, bracknell=False) == "warn"
        assert commute_colour(76, bracknell=False) == "bad"

    def test_bracknell_good(self):
        assert commute_colour(25, bracknell=True) == "good"

    def test_bracknell_warn(self):
        assert commute_colour(30, bracknell=True) == "warn"

    def test_bracknell_bad(self):
        assert commute_colour(65, bracknell=True) == "bad"

    def test_none_returns_muted(self):
        assert commute_colour(None, bracknell=False) == "muted"


class TestOfstedColour:
    def test_outstanding_is_good(self):
        assert ofsted_colour("Outstanding") == "good"

    def test_good_is_warn(self):
        assert ofsted_colour("Good") == "warn"

    def test_requires_improvement_is_bad(self):
        assert ofsted_colour("Requires Improvement") == "bad"

    def test_inadequate_is_bad(self):
        assert ofsted_colour("Inadequate") == "bad"

    def test_empty_returns_muted(self):
        assert ofsted_colour("") == "muted"


class TestWalkColour:
    def test_good_under_15(self):
        assert walk_colour(10) == "good"

    def test_boundary_good_warn(self):
        assert walk_colour(14) == "good"
        assert walk_colour(15) == "warn"

    def test_boundary_warn_bad(self):
        assert walk_colour(30) == "warn"
        assert walk_colour(31) == "bad"

    def test_none_returns_muted(self):
        assert walk_colour(None) == "muted"


# ── Card building ────────────────────────────────────────────────────────


class TestCardBuild:
    def test_address_and_price(self):
        card = _build_card(_data(), _view())
        assert card.address == "48 Acacia Avenue, Southall, UB2"
        assert card.price == 450000.0

    def test_bedrooms_and_postcode_district(self):
        card = _build_card(_data(), _view())
        assert card.bedrooms == 3
        assert card.postcode_district == "UB2"

    def test_commute_colours(self):
        card = _build_card(_data(), _view())
        assert card.simon_colour == "good"  # 32 < 45
        assert card.lorena_colour == "warn"  # 45 is boundary (warn)
        assert card.bracknell_colour == "good"  # 22 < 30

    def test_commute_duration_formatting(self):
        card = _build_card(_data(), _view())
        assert card.simon_dur == "32m"
        assert card.lorena_dur == "45m"
        assert card.bracknell_dur == "22m"

    def test_long_commute_formats_as_hours(self):
        card = _build_card(_data({"Simon London (min)": "90"}), _view())
        assert card.simon_dur == "1h30"

    def test_exact_hour_commute(self):
        card = _build_card(_data({"Simon London (min)": "60"}), _view())
        assert card.simon_dur == "1h"

    def test_ofsted_colours(self):
        card = _build_card(_data(), _view())
        assert card.primary_ofsted_label == "Outstanding"
        assert card.primary_ofsted_colour == "good"
        assert card.secondary_ofsted_label == "Good"
        assert card.secondary_ofsted_colour == "warn"

    def test_walk_to_town(self):
        card = _build_card(_data(), _view())
        assert card.walk_to_town_minutes == 10
        assert card.walk_colour == "good"

    def test_town_name_extracted_from_address(self):
        card = _build_card(_data(), _view())
        assert card.town_name == "Southall"

    def test_town_excludes_counties(self):
        card = _build_card(_data({"Address": "Some Road, Maidenhead, Berkshire, SL6"}), _view())
        assert card.town_name == "Maidenhead"

    def test_school_name_trimmed_at_comma(self):
        card = _build_card(
            _data({"Primary School": "St Paul's Church of England Combined School, Wooburn"}),
            _view(),
        )
        assert card.primary_name == "St Paul's Church of England Combined School"

    def test_ofsted_first_word_no_punctuation(self):
        card = _build_card(_data({"Primary Ofsted": "Good, Behaviour Outstanding"}), _view())
        assert card.primary_ofsted_label == "Good"

    def test_bus_priority_for_secondary(self):
        card = _build_card(_data({"Secondary Walk (min)": "23", "Secondary Bus (min)": "14"}), _view())
        assert card.secondary_walk_label == "14m bus"

    def test_fallback_to_walk_when_no_bus(self):
        card = _build_card(
            _data({"Secondary Walk (min)": "12", "Secondary Bus (min)": ""}),
            _view(),
        )
        assert card.secondary_walk_label == "12m walk"

    def test_current_home_status(self):
        card = _build_card(_data(), _view({"Status": "Current"}))
        assert card.status == "Current"

    def test_unenriched_card(self):
        data = _data(
            {
                "Simon London (min)": "",
                "Lorena London (min)": "",
                "Bracknell Time (min)": "",
            }
        )
        card = _build_card(data, _view())
        assert card.is_enriched is False
        assert card.simon_minutes is None
        assert card.simon_colour == "muted"

    def test_direction_urls_present_when_coords_known(self):
        card = _build_card(_data(), _view())
        assert card.simon_dir_url.startswith("https://www.google.com/maps/dir/51.5,-0.4/")
        assert card.walk_dir_url.startswith("https://www.google.com/maps/dir/51.5,-0.4/")
        assert card.primary_dir_url.startswith("https://www.google.com/maps/dir/51.5,-0.4/")

    def test_direction_urls_empty_when_no_coords(self):
        card = _build_card(_data({"Best Latitude": "", "Best Longitude": ""}), _view())
        assert card.simon_dir_url == ""
        assert card.walk_dir_url == ""

    def test_total_monthly_cost(self):
        card = _build_card(_data(), _view({"Total Monthly Housing Cost (£)": "2850.00"}))
        assert card.total_monthly_cost == 2850.0

    def test_score_all_green(self):
        data = _data(
            {
                "Simon London (min)": "30",
                "Lorena London (min)": "30",
                "Bracknell Time (min)": "20",
                "Primary Ofsted": "Outstanding",
                "Primary Walk (min)": "5",
                "Secondary Ofsted": "Outstanding",
                "Secondary Walk (min)": "5",
                "Walk to Town (min)": "5",
            }
        )
        card = _build_card(data, _view())
        assert card.score == 16  # 8 × 2

    def test_score_mixed(self):
        """Score computed: green=2, orange=1, red=-1, muted=0."""
        data = _data(
            {
                "Simon London (min)": "30",  # good (2)
                "Lorena London (min)": "50",  # warn (1)
                "Bracknell Time (min)": "20",  # good (2)
                "Primary Ofsted": "Outstanding",  # good (2)
                "Primary Walk (min)": "5",  # good (2)
                "Secondary Ofsted": "Good",  # warn (1)
                "Secondary Walk (min)": "5",  # good (2)
                "Walk to Town (min)": "5",  # good (2)
            }
        )
        card = _build_card(data, _view())
        assert card.score == 14

    def test_score_with_reds(self):
        """Red commutes and Ofsted subtract a point.

        Other defaults (walk times, secondary Ofsted) are cleared so
        only the bad values contribute.
        """
        data = _data(
            {
                "Simon London (min)": "90",  # bad (-1)
                "Lorena London (min)": "80",  # bad (-1)
                "Bracknell Time (min)": "70",  # bad (-1)
                "Primary Ofsted": "Inadequate",  # bad (-1)
                "Primary Walk (min)": "",
                "Secondary Ofsted": "",
                "Secondary Walk (min)": "",
                "Walk to Town (min)": "",
            }
        )
        card = _build_card(data, _view())
        assert card.score == -4  # 4 bad × -1

    def test_score_muted_contributes_zero(self):
        """Missing data (muted) contributes 0, not -1."""
        card = _build_card(_data(), _view())
        assert card.score == 14


# ── Card sorting (with mocked I/O) ────────────────────────────────────────


class TestCardSorting:
    def test_cards_sorted_by_score_descending(self):
        data_best = _data({"Rightmove ID": "best", "Simon London (min)": "20"})
        data_worst = _data({"Rightmove ID": "worst", "Simon London (min)": "95"})
        data_rows = [data_worst, data_best]
        view_rows = [_view({"Rightmove ID": "best"}), _view({"Rightmove ID": "worst"})]

        with mock.patch("houses.web.card_data.get_data_rows", return_value=data_rows), \
             mock.patch("houses.web.card_data.get_view_rows", return_value=view_rows):
            cards = get_all_cards()

        assert len(cards) == 2
        assert cards[0].rid == "best"
        assert cards[1].rid == "worst"
