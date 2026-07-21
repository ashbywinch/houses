"""Tests for Commute — leg descriptions must be consistent across APIs.

TfL and Google Routes both set ``JourneyLeg`` raw fields (mode, line_name,
end_station, start_station).  ``_render_leg_description`` renders them
in a consistent format, always showing the destination.
"""

from __future__ import annotations

from houses.commute import JourneyLeg, LegMode, _render_leg_description


class TestLegDescription:
    """JourneyLeg descriptions must be consistent regardless of source API."""

    def test_walk_with_end_station(self):
        leg = JourneyLeg(mode=LegMode.WALK, duration_minutes=10, end_station="Maidenhead")
        desc = _render_leg_description(leg)
        assert desc == "walk to Maidenhead"

    def test_walk_without_end_station(self):
        leg = JourneyLeg(mode=LegMode.WALK, duration_minutes=5)
        desc = _render_leg_description(leg)
        assert desc == "walk"

    def test_line_name_with_station(self):
        leg = JourneyLeg(mode=LegMode.TUBE, duration_minutes=4, line_name="Victoria", end_station="Oxford Circus")
        desc = _render_leg_description(leg)
        assert desc == "Victoria to Oxford Circus"

    def test_line_name_without_station(self):
        leg = JourneyLeg(mode=LegMode.BUS, duration_minutes=15, line_name="Route 3")
        desc = _render_leg_description(leg)
        assert desc == "Route 3"

    def test_mode_with_end_station(self):
        leg = JourneyLeg(mode=LegMode.TRAIN, duration_minutes=20, end_station="Paddington")
        desc = _render_leg_description(leg)
        assert desc == "train to Paddington"

    def test_mode_only(self):
        leg = JourneyLeg(mode=LegMode.CYCLE, duration_minutes=30)
        desc = _render_leg_description(leg)
        assert desc == "cycle"
