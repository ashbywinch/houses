"""Tests for card data — API response shape for the frontend.

The old card_data module (``_build_card`` / ``CardData`` dataclass) has been
replaced by the DAG-based ``PropertyNode.to_json_summary()``.  Card display
is now driven by the frontend consuming the API response JSON.

These tests verify that the API response shape matches what the frontend
``PropertyCard.vue`` and ``PropertyDetail.vue`` expect, plus the pure
helper functions that survived the migration.
"""

from __future__ import annotations

import pytest
from money import Money
from pint import Quantity

from dag.attempt import Attempt
from dag.scheduler import flush_processor
from houses.geopoint import GeoPoint
from houses.model.domain import Commute, Person, PlaceOfInterest
from houses.nodes.commute import commute_colour, format_duration
from houses.nodes.property_nodes import PropertyNodes
from houses.property_registry import register_property
from houses.web.api_router import _score_from_summary
from tests.helpers import make_services

# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _mock():
    """Set fake services with a commute router that returns canned data."""
    from houses.services_provider import _request_services as _sp

    class _CannedPlanner:
        async def walk_route(self, origin, destination, max_walk):
            return Attempt.succeeded(
                Commute(
                    person=Person(name="Test", has_car=False),
                    label="Walk",
                    destination=PlaceOfInterest(label="Dest", address=str(destination)),
                    duration=Quantity(30, "minute"),
                    daily_cost=Money("0", "GBP"),
                ),
            )

        async def drive_route(self, origin, destination):
            return Attempt.succeeded(
                Commute(
                    person=Person(name="Test", has_car=True),
                    label="Drive",
                    destination=PlaceOfInterest(label="Dest", address=str(destination)),
                    duration=Quantity(20, "minute"),
                    daily_cost=Money("5.0", "GBP"),
                ),
            )

    canned = Commute(
        person=Person(name="Test", has_car=False),
        label="Test",
        destination=PlaceOfInterest(label="Dest", address="SW1V 2QQ"),
        duration=Quantity(30, "minute"),
        daily_cost=Money("5.0", "GBP"),
        mode="transit",
    )

    class _FakeTflClient:
        """Canned transit plan — injected via the services client factory."""

        def __init__(self, *args, **kwargs):
            self._plan_override = None
            self._no_route_detail = ""

        async def plan(self):
            return Attempt.succeeded(canned)

    svc = make_services(route_planner=_CannedPlanner(), tfl_client_factory=_FakeTflClient)
    token = _sp.set(svc)
    yield
    _sp.reset(token)


@pytest.fixture(autouse=True)
def _clear():
    from houses.services_provider import get_services

    registry = get_services().property_registry
    registry.clear()
    yield
    registry.clear()


@pytest.fixture
def prop():
    """A PropertyNode seeded with basic data, registered."""
    rid = "test_card"
    p = PropertyNodes(rid)
    p.rightmove_address.push("48 Acacia Avenue, Southall, UB2 5AD", "Rightmove")
    p.rightmove_url.push("https://www.rightmove.co.uk/properties/12345", "Browser")
    p.rightmove_bedrooms.push("3", "Rightmove")
    p.rightmove_price.push(Money("450000", "GBP"), "Rightmove")
    p.rightmove_location.push(GeoPoint(51.5, -0.4), "Rightmove map")
    p.postcode.push("UB2 5AD", "Rightmove")
    register_property(rid, p)
    return p


# ── Colour helpers ──────────────────────────────────────────────────────


class TestCommuteColour:
    """commute_colour() survived the migration — still used in scoring."""

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


class TestFormatDuration:
    """format_duration() replaced the old ``_dur`` helper.

    Frontend still formats commute durations in ``PropertyCard.vue`` via
    ``commuteDuration()``, but the API response includes raw minutes.
    This helper is used internally in the DAG pipeline.
    """

    def test_none_returns_empty(self):
        assert format_duration(None) == ""

    def test_under_one_hour(self):
        assert format_duration(32) == "32m"

    def test_exact_one_hour(self):
        assert format_duration(60) == "1h"

    def test_one_hour_with_minutes(self):
        assert format_duration(90) == "1h30"

    def test_two_hours_exact(self):
        assert format_duration(120) == "2h"

    def test_two_hours_with_minutes(self):
        assert format_duration(145) == "2h25"


class TestOfstedColour:
    """_ofsted_score() from the scoring routine replicates the old ofsted_colour() mapping.

    The colour helper itself lives in the frontend;
    the backend scoring uses the same thresholds.
    """

    @staticmethod
    def _summary_with_ofsted(ofsted: str) -> dict:
        return {
            "commutes": {},
            "schools": {
                "primary": {"school": {"status": "succeeded", "value": {"ofsted": ofsted, "walk": None}}},
                "secondary": {"school": {"status": "impossible", "value": None}},
            },
            "walkability": {"value": None},
        }

    def test_outstanding_is_good(self):
        """Outstanding → score 2 (was colour 'good')."""
        assert _score_from_summary(self._summary_with_ofsted("Outstanding")) == 2

    def test_good_is_warn(self):
        """Good → score 1 (was colour 'warn')."""
        assert _score_from_summary(self._summary_with_ofsted("Good")) == 1

    def test_requires_improvement_is_bad(self):
        """Requires Improvement → score -1 (was colour 'bad')."""
        assert _score_from_summary(self._summary_with_ofsted("Requires Improvement")) == -1

    def test_inadequate_is_bad(self):
        """Inadequate → score -1 (was colour 'bad')."""
        assert _score_from_summary(self._summary_with_ofsted("Inadequate")) == -1

    def test_empty_returns_muted(self):
        """Empty → score 0 (was colour 'muted')."""
        assert _score_from_summary(self._summary_with_ofsted("")) == 0


class TestWalkColour:
    """_walk_score() from the scoring routine replicates the old walk_colour() mapping.

    Colour is now a frontend concern; the backend scoring still uses the
    same thresholds (<15 green, 15-30 warn, >30 bad).
    """

    @staticmethod
    def _summary_with_walk(minutes: int | None) -> dict:
        return {
            "commutes": {},
            "schools": {
                "primary": {"school": {"status": "impossible", "value": None}},
                "secondary": {"school": {"status": "impossible", "value": None}},
            },
            "walkability": {"value": {"walk_to_town": {"value": minutes, "unit": "minute"}}},
        }

    def test_good_under_15(self):
        """walk < 15 → score 2 (was colour 'good')."""
        assert _score_from_summary(self._summary_with_walk(10)) == 2

    def test_boundary_good_warn(self):
        """14 → green/2, 15 → warn/1."""
        assert _score_from_summary(self._summary_with_walk(14)) == 2
        assert _score_from_summary(self._summary_with_walk(15)) == 1

    def test_boundary_warn_bad(self):
        """30 → warn/1, 31 → bad/-1."""
        assert _score_from_summary(self._summary_with_walk(30)) == 1
        assert _score_from_summary(self._summary_with_walk(31)) == -1

    def test_none_returns_muted(self):
        """None → score 0 (was colour 'muted')."""
        assert _score_from_summary(self._summary_with_walk(None)) == 0


# ── API response shape ──────────────────────────────────────────────────


class TestSummaryShape:
    """Verify the top-level keys of to_json_summary()."""

    @pytest.mark.asyncio
    async def test_has_expected_top_level_keys(self, prop):
        await flush_processor()
        await flush_processor()
        s = await prop.to_json_summary()

        assert s["rid"] == "test_card"
        for key in (
            "best_address",
            "best_location",
            "rightmove_price",
            "rightmove_bedrooms",
            "group_monthly_cost",
            "town_name",
            "commutes",
            "schools",
            "walkability",
        ):
            assert key in s, f"Missing key: {key}"

    @pytest.mark.asyncio
    async def test_every_wrapped_value_has_status_and_value(self, prop):
        """Every node-backed field wraps its value in a standard envelope (no provenance in summary)."""
        await flush_processor()
        await flush_processor()
        s = await prop.to_json_summary()

        wrapped = (
            "best_address",
            "best_location",
            "rightmove_price",
            "rightmove_bedrooms",
            "group_monthly_cost",
            "town_name",
            "walkability",
        )
        for key in wrapped:
            val = s[key]
            assert "status" in val, f"{key} missing status"
            assert "value" in val, f"{key} missing value"


class TestCommuteData:
    """Commute entries in the summary must match frontend expectations."""

    @pytest.mark.asyncio
    async def test_commutes_is_dict(self, prop):
        await flush_processor()
        await flush_processor()
        s = await prop.to_json_summary()
        assert isinstance(s["commutes"], dict)

    @pytest.mark.asyncio
    async def test_each_commute_has_commute_key(self, prop):
        await flush_processor()
        await flush_processor()
        s = await prop.to_json_summary()

        for key, cd in s["commutes"].items():
            assert "commute" in cd, f"{key} missing 'commute'"
            c = cd["commute"]
            assert "status" in c, f"{key} commute missing status"
            assert "succeeded" in c, f"{key} commute missing succeeded"

    @pytest.mark.asyncio
    async def test_successful_commute_has_duration_and_cost(self, prop):
        await flush_processor()
        await flush_processor()
        s = await prop.to_json_summary()

        for key, cd in s["commutes"].items():
            c = cd["commute"]
            if not c.get("succeeded"):
                continue
            val = c.get("value")
            assert isinstance(val, dict), f"{key} value should be a dict"
            assert "duration" in val, f"{key} value missing duration"
            assert "daily_cost" in val, f"{key} value missing daily_cost"
            # Duration
            dur = val.get("duration", {})
            assert "value" in dur, f"{key} duration missing value"
            assert dur.get("unit") == "minute", f"{key} duration unit not minute"
            assert isinstance(dur["value"], int) and dur["value"] > 0
            # Daily cost
            dc = val.get("daily_cost", {})
            assert "amount" in dc, f"{key} daily_cost missing amount"
            assert "currency" in dc, f"{key} daily_cost missing currency"

    @pytest.mark.asyncio
    async def test_commute_has_is_child_flag(self, prop):
        await flush_processor()
        await flush_processor()
        s = await prop.to_json_summary()

        for key, cd in s["commutes"].items():
            c = cd["commute"]
            assert "is_child" in c, f"{key} commute missing is_child"

    @pytest.mark.asyncio
    async def test_child_commutes_marked_is_child(self, prop):
        """School commutes (George/Primary School etc.) carry is_child=True."""
        await flush_processor()
        await flush_processor()
        s = await prop.to_json_summary()

        for key, cd in s["commutes"].items():
            c = cd["commute"]
            if "Primary School" in key or "Secondary School" in key:
                assert c.get("is_child") is True, f"{key} should be child commute"


class TestSchoolData:
    """School entries in the summary match what the frontend renders."""

    @pytest.mark.asyncio
    async def test_schools_has_primary_and_secondary(self, prop):
        await flush_processor()
        await flush_processor()
        s = await prop.to_json_summary()

        schools = s.get("schools", {})
        assert "primary" in schools
        assert "secondary" in schools
        for phase in ("primary", "secondary"):
            assert "school" in schools[phase], f"{phase} missing 'school' key"

    @pytest.mark.asyncio
    async def test_each_school_has_name_ofsted_and_url(self, prop):
        await flush_processor()
        await flush_processor()
        s = await prop.to_json_summary()

        for phase in ("primary", "secondary"):
            school_node = s["schools"][phase]["school"]
            val = school_node.get("value")
            assert isinstance(val, dict), f"{phase} school value should be a dict"
            assert "name" in val, f"{phase} school value missing name"
            assert "ofsted" in val, f"{phase} school value missing ofsted"
            assert "url" in val, f"{phase} school value missing url"
            assert "walk" in val, f"{phase} school value missing walk"

    @pytest.mark.asyncio
    async def test_school_data_envelope(self, prop):
        """School nodes themselves have a status/value/provenance envelope."""
        await flush_processor()
        await flush_processor()
        s = await prop.to_json_summary()

        for phase in ("primary", "secondary"):
            school_node = s["schools"][phase]["school"]
            assert "status" in school_node, f"{phase} school missing status"
            assert "succeeded" in school_node, f"{phase} school missing succeeded"


# ── Scoring ─────────────────────────────────────────────────────────────


class TestScoring:
    """_score_from_summary() replicates the old card_data scoring formula."""

    @pytest.mark.asyncio
    async def test_score_is_integer(self, prop):
        await flush_processor()
        await flush_processor()
        s = await prop.to_json_summary()
        score = _score_from_summary(s)
        assert score == 16

    def test_all_green_returns_max(self):
        summary = {
            "commutes": {
                "Simon/Pimlico": {
                    "commute": {"status": "succeeded", "value": {"duration": {"value": 30, "unit": "minute"}}}
                },
                "Lorena/Aldgate": {
                    "commute": {"status": "succeeded", "value": {"duration": {"value": 30, "unit": "minute"}}}
                },
                "Simon/Bracknell": {
                    "commute": {"status": "succeeded", "value": {"duration": {"value": 20, "unit": "minute"}}}
                },
            },
            "schools": {
                "primary": {
                    "school": {
                        "status": "succeeded",
                        "value": {"ofsted": "Outstanding", "walk": {"value": 5, "unit": "minute"}},
                    }
                },
                "secondary": {
                    "school": {
                        "status": "succeeded",
                        "value": {"ofsted": "Outstanding", "walk": {"value": 5, "unit": "minute"}},
                    }
                },
            },
            "walkability": {"value": {"walk_to_town": {"value": 5, "unit": "minute"}}},
        }
        score = _score_from_summary(summary)
        assert score == 16  # 8 metrics × 2

    def test_greens_and_warns_mixed(self):
        summary = {
            "commutes": {
                "Simon/Pimlico": {
                    "commute": {"status": "succeeded", "value": {"duration": {"value": 30, "unit": "minute"}}}
                },
                "Lorena/Aldgate": {
                    "commute": {"status": "succeeded", "value": {"duration": {"value": 50, "unit": "minute"}}}
                },
                "Simon/Bracknell": {
                    "commute": {"status": "succeeded", "value": {"duration": {"value": 20, "unit": "minute"}}}
                },
            },
            "schools": {
                "primary": {
                    "school": {
                        "status": "succeeded",
                        "value": {"ofsted": "Outstanding", "walk": {"value": 5, "unit": "minute"}},
                    }
                },
                "secondary": {
                    "school": {
                        "status": "succeeded",
                        "value": {"ofsted": "Good", "walk": {"value": 5, "unit": "minute"}},
                    }
                },
            },
            "walkability": {"value": {"walk_to_town": {"value": 5, "unit": "minute"}}},
        }
        score = _score_from_summary(summary)
        assert score == 14  # 2×3 + 1 + 2×3 + 2 + 1 + 2

    def test_bad_values_subtract(self):
        summary = {
            "commutes": {
                "Simon/Pimlico": {
                    "commute": {"status": "succeeded", "value": {"duration": {"value": 90, "unit": "minute"}}}
                },
                "Lorena/Aldgate": {
                    "commute": {"status": "succeeded", "value": {"duration": {"value": 80, "unit": "minute"}}}
                },
                "Simon/Bracknell": {
                    "commute": {"status": "succeeded", "value": {"duration": {"value": 70, "unit": "minute"}}}
                },
            },
            "schools": {
                "primary": {
                    "school": {
                        "status": "succeeded",
                        "value": {"ofsted": "Inadequate", "walk": {"value": 50, "unit": "minute"}},
                    }
                },
                "secondary": {"school": {"status": "succeeded", "value": {"ofsted": "", "walk": None}}},
            },
            "walkability": {"value": {"walk_to_town": None}},
        }
        score = _score_from_summary(summary)
        assert score == -5  # 3 red commutes (-1 each) + red ofsted (-1) + bad walk (-1)

    def test_muted_contributes_zero(self):
        summary = {
            "commutes": {},
            "schools": {
                "primary": {"school": {"status": "impossible", "value": None}},
                "secondary": {"school": {"status": "impossible", "value": None}},
            },
            "walkability": {"value": None},
        }
        score = _score_from_summary(summary)
        assert score == 0

    def test_bracknell_thresholds(self):
        """Bracknell commutes use 30/60 thresholds instead of 45/75."""
        summary = {
            "commutes": {
                "Simon/Bracknell": {
                    "commute": {"status": "succeeded", "value": {"duration": {"value": 25, "unit": "minute"}}}
                },
            },
            "schools": {
                "primary": {"school": {"status": "impossible", "value": None}},
                "secondary": {"school": {"status": "impossible", "value": None}},
            },
            "walkability": {"value": None},
        }
        assert _score_from_summary(summary) == 2  # green = 2
        summary["commutes"]["Simon/Bracknell"]["commute"]["value"]["duration"]["value"] = 35
        assert _score_from_summary(summary) == 1  # warn = 1
        summary["commutes"]["Simon/Bracknell"]["commute"]["value"]["duration"]["value"] = 65
        assert _score_from_summary(summary) == -1  # bad = -1


class TestCardSorting:
    """Cards/properties sorted by score descending (matching old get_all_cards())."""

    def test_sorted_by_score_descending(self):
        """Verify the sorting logic used by get_all_properties()."""
        high = {
            "commutes": {},
            "schools": {
                "primary": {"school": {"status": "impossible", "value": None}},
                "secondary": {"school": {"status": "impossible", "value": None}},
            },
            "walkability": {"value": None},
        }
        mid = {
            "commutes": {},
            "schools": {
                "primary": {
                    "school": {
                        "status": "succeeded",
                        "value": {"ofsted": "Outstanding", "walk": {"value": 5, "unit": "minute"}},
                    }
                },
                "secondary": {
                    "school": {
                        "status": "succeeded",
                        "value": {"ofsted": "Good", "walk": {"value": 5, "unit": "minute"}},
                    }
                },
            },
            "walkability": {"value": None},
        }
        low = {
            "commutes": {
                "Simon/Pimlico": {
                    "commute": {"status": "succeeded", "value": {"duration": {"value": 90, "unit": "minute"}}}
                },
            },
            "schools": {
                "primary": {"school": {"status": "impossible", "value": None}},
                "secondary": {"school": {"status": "impossible", "value": None}},
            },
            "walkability": {"value": None},
        }

        results = {"low": low, "high": high, "mid": mid}
        scored = sorted(results.items(), key=lambda kv: _score_from_summary(kv[1]), reverse=True)
        assert [r[0] for r in scored] == ["mid", "high", "low"]
