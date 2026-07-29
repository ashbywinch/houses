"""Tests for Commute — leg descriptions must be consistent across APIs.

TfL and Google Routes both set ``JourneyLeg`` raw fields (mode, line_name,
end_station, start_station).  ``_render_leg_description`` renders them
in a consistent format, always showing the destination.
"""

from __future__ import annotations

from pint import Quantity

from houses.commute import JourneyLeg, LegMode, _render_leg_description


class TestLegDescription:
    """JourneyLeg descriptions must be consistent regardless of source API."""

    def test_walk_with_end_station(self):
        leg = JourneyLeg(mode=LegMode.WALK, duration=Quantity(10, "minute"), end_station="Maidenhead")
        desc = _render_leg_description(leg)
        assert desc == "walk to Maidenhead"

    def test_walk_without_end_station(self):
        leg = JourneyLeg(mode=LegMode.WALK, duration=Quantity(5, "minute"))
        desc = _render_leg_description(leg)
        assert desc == "walk"

    def test_line_name_with_station(self):
        leg = JourneyLeg(
            mode=LegMode.TUBE, duration=Quantity(4, "minute"), line_name="Victoria", end_station="Oxford Circus"
        )
        desc = _render_leg_description(leg)
        assert desc == "Victoria to Oxford Circus"

    def test_line_name_without_station(self):
        leg = JourneyLeg(mode=LegMode.BUS, duration=Quantity(15, "minute"), line_name="Route 3")
        desc = _render_leg_description(leg)
        assert desc == "Route 3"

    def test_mode_with_end_station(self):
        leg = JourneyLeg(mode=LegMode.TRAIN, duration=Quantity(20, "minute"), end_station="Paddington")
        desc = _render_leg_description(leg)
        assert desc == "train to Paddington"

    def test_mode_only(self):
        leg = JourneyLeg(mode=LegMode.CYCLE, duration=Quantity(30, "minute"))
        desc = _render_leg_description(leg)
        assert desc == "cycle"
