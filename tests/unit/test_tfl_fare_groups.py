"""Tests for TflClient._build_cost_groups — the TfL totalCost must land
on the transit CostGroups.

Regression: 88275093 Simon/Pimlico showed "Could not calculate — no
fare STL→EAL". The route (Southall → Ealing Broadway → tube → Pimlico)
is entirely TfL-priced, but the fare was DROPPED in _build_cost_groups
(a dangling `round(fare['totalCost']…)` expression), so daily_cost was
£0 and the National Rail fare node ran, found no NR fare for the
Elizabeth-line leg, and killed the commute.
"""

from __future__ import annotations

import asyncio

from houses.tfl_client import TflClient

# A TfL journey: walk → national-rail (Elizabeth line) → tube → walk.
# TfL prices the whole transit journey with fare.totalCost (pence).
_TFL_JOURNEY = {
    "journeys": [
        {
            "duration": 42,
            "fare": {
                "totalCost": 720,  # 720p = £7.20 single → £14.40 return
                "fares": [
                    {"mode": "national-rail", "cost": 400},
                    {"mode": "tube", "cost": 320},
                ],
            },
            "legs": [
                {
                    "mode": {"name": "walking"},
                    "duration": "8",
                    "departurePoint": {"commonName": "Isambard Road"},
                    "arrivalPoint": {"commonName": "Southall Rail Station"},
                    "route": {"name": ""},
                    "instruction": {"summary": "Walk"},
                },
                {
                    "mode": {"name": "national-rail"},
                    "duration": "9",
                    "departurePoint": {"commonName": "Southall Rail Station"},
                    "arrivalPoint": {"commonName": "Ealing Broadway Rail Station"},
                    "route": {"name": "Elizabeth line"},
                    "instruction": {"summary": "Elizabeth line"},
                },
                {
                    "mode": {"name": "tube"},
                    "duration": "14",
                    "departurePoint": {"commonName": "Ealing Broadway Underground Station"},
                    "arrivalPoint": {"commonName": "Pimlico Underground Station"},
                    "route": {"name": "Central"},
                    "instruction": {"summary": "Central line"},
                },
                {
                    "mode": {"name": "walking"},
                    "duration": "6",
                    "departurePoint": {"commonName": "Pimlico Underground Station"},
                    "arrivalPoint": {"commonName": "1 Drummond Gate"},
                    "route": {"name": ""},
                    "instruction": {"summary": "Walk"},
                },
            ],
        }
    ]
}


class TestBuildCostGroupsAppliesFare:
    def _client(self) -> TflClient:
        return TflClient("51.5012,-0.3686", "1 Drummond Gate, Pimlico, London SW1V 2QQ", "Pimlico")

    def test_transit_groups_carry_the_tfl_total_cost(self):
        """The journey's TfL totalCost must be on the transit CostGroups —
        a TfL route must never present a £0 cost that forces the National
        Rail fare node to run."""
        groups = self._client()._build_cost_groups(_TFL_JOURNEY)
        transit = [g for g in groups if g.operator == "TfL"]
        assert transit, "expected at least one TfL cost group"
        costs = [g.cost for g in transit]
        assert any(c is not None for c in costs), f"transit groups must carry a cost, got {costs}"

    def test_daily_cost_derives_from_tfl_fare(self):
        """_process_data must end up with a nonzero daily_cost for a
        priced TfL route — otherwise the rail-fare node wrongly runs."""
        async def run():
            attempt = await self._client()._process_data(_TFL_JOURNEY)
            assert attempt.succeeded
            commute = attempt.value_or_none()
            assert commute is not None
            assert float(commute.daily_cost.amount) > 0, (
                f"TfL route must carry its fare, got £{commute.daily_cost.amount}"
            )

        asyncio.run(run())
