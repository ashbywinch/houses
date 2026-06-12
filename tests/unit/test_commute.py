"""Tests for Commute — leg descriptions must be consistent across APIs.

TfL and Google Routes both set ``JourneyLeg`` raw fields (mode, line_name,
end_station, start_station).  ``Commute._leg_description`` and ``summary()``
render them in a consistent format, always showing the destination.
"""

from __future__ import annotations

from houses.commute import Commute, CostGroup, JourneyLeg, LegMode

# ── _leg_description format ─────────────────────────────────────────────


class TestLegDescription:
    """JourneyLeg descriptions must be consistent regardless of source API."""

    def test_walk_with_end_station(self):
        """Walk leg with end_station → 'walk to {station}'."""
        leg = JourneyLeg(mode=LegMode.WALK, duration_minutes=10, end_station="Maidenhead Rail Station")
        c = Commute(destination_label="", destination_postcode="")
        desc = c._leg_description(leg)
        assert desc == "walk to Maidenhead"

    def test_walk_last_leg(self):
        """Final walk leg should just show 'walk {min}' in summary, not 'walk to nowhere'."""
        leg = JourneyLeg(mode=LegMode.WALK, duration_minutes=5)
        c = Commute(
            destination_label="", destination_postcode="",
            cost_groups=(CostGroup(legs=(leg,)),),
        )
        assert c.summary() == "walk 5m"

    def test_bus_with_line_and_destination(self):
        """Bus leg with line_name + end_station → '{line} to {station}'."""
        leg = JourneyLeg(mode=LegMode.BUS, duration_minutes=12, line_name="564", end_station="Claremont Avenue")
        c = Commute(destination_label="", destination_postcode="")
        desc = c._leg_description(leg)
        assert desc == "564 to Claremont Avenue"

    def test_train_with_line_and_destination(self):
        """Train leg with line_name + end_station → '{line} to {station}'."""
        leg = JourneyLeg(
            mode=LegMode.TRAIN, duration_minutes=28,
            line_name="South Western Railway", end_station="Waterloo",
        )
        c = Commute(destination_label="", destination_postcode="")
        desc = c._leg_description(leg)
        assert desc == "South Western Railway to Waterloo"

    def test_bus_without_line_name(self):
        """Bus leg without line_name → 'bus to {station}'."""
        leg = JourneyLeg(mode=LegMode.BUS, duration_minutes=5, end_station="Pimlico")
        c = Commute(destination_label="", destination_postcode="")
        desc = c._leg_description(leg)
        assert desc == "bus to Pimlico"

    def test_bus_without_line_or_station(self):
        """Bus leg with neither → fallback to description field or mode name."""
        leg = JourneyLeg(mode=LegMode.BUS, duration_minutes=5)
        c = Commute(destination_label="", destination_postcode="")
        desc = c._leg_description(leg)
        assert desc == "bus"

    def test_tube_with_line_and_destination(self):
        """Tube leg → '{line} to {station}'."""
        leg = JourneyLeg(mode=LegMode.TUBE, duration_minutes=8, line_name="Victoria", end_station="Oxford Circus")
        c = Commute(destination_label="", destination_postcode="")
        desc = c._leg_description(leg)
        assert desc == "Victoria to Oxford Circus"


# ── Full route summary ──────────────────────────────────────────────────


class TestRouteSummary:
    """Full route summaries with mixed leg types must be consistent."""

    def test_tfl_style_route(self):
        """TfL-style route: walk → bus → train → walk."""
        legs = (
            JourneyLeg(mode=LegMode.WALK, duration_minutes=2, end_station="Greenway"),
            JourneyLeg(mode=LegMode.BUS, duration_minutes=9, line_name="163", end_station="Raynes Park"),
            JourneyLeg(
                mode=LegMode.TRAIN, duration_minutes=16,
                line_name="South Western Railway", end_station="Vauxhall",
            ),
            JourneyLeg(mode=LegMode.WALK, duration_minutes=7, end_station="Vauxhall Bus Station"),
            JourneyLeg(mode=LegMode.BUS, duration_minutes=3, line_name="36", end_station="Pimlico"),
            JourneyLeg(mode=LegMode.WALK, duration_minutes=2),
        )
        commute = Commute(
            destination_label="", destination_postcode="",
            duration_minutes=39,
            cost_groups=(CostGroup(legs=legs),),
        )
        summary = commute.summary()
        assert "walk to Greenway (2m)" in summary
        assert "163 to Raynes Park (9m)" in summary, f"Got: {summary}"
        assert "South Western Railway to Vauxhall (16m)" in summary
        assert "walk to Vauxhall Bus (7m)" in summary
        assert "36 to Pimlico (3m)" in summary
        assert summary.endswith("walk 2m"), f"Should end with walk, got: {summary}"

    def test_old_format_backwards(self):
        """Legs with pre-baked descriptions still work (backwards compat)."""
        legs = (
            JourneyLeg(mode=LegMode.WALK, duration_minutes=19),
            JourneyLeg(
                mode=LegMode.TRAIN, duration_minutes=23,
                line_name="South Western Railway", end_station="Waterloo",
            ),
            JourneyLeg(mode=LegMode.TUBE, duration_minutes=4, line_name="Waterloo & City", end_station="Bank"),
            JourneyLeg(mode=LegMode.WALK, duration_minutes=18),
        )
        commute = Commute(
            destination_label="", destination_postcode="",
            duration_minutes=64,
            cost_groups=(CostGroup(legs=legs),),
        )
        summary = commute.summary()
        assert "South Western Railway to Waterloo" in summary
        assert "Waterloo & City to Bank" in summary
        assert summary.endswith("walk 18m")
