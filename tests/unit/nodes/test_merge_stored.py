"""Failing first: merge formula shows calculating inputs, never live re-reads."""

from __future__ import annotations

from money import Money
from pint import Quantity

from houses.commute import CostGroup, JourneyLeg, LegMode
from houses.model.domain import Commute, Person, PlaceOfInterest
from houses.nodes.commute import MergeRailFareNode
from tests.unit.conftest import flush_all


def _commute(daily: str) -> Commute:
    office = PlaceOfInterest("Office", "SW1P 1AA")
    leg = JourneyLeg(mode=LegMode.TRAIN, duration=Quantity(30, "minute"))
    return Commute(
        person=Person("Simon", True, places_of_interest=(office,)),
        label=office.label,
        destination=office,
        duration=Quantity(30, "minute"),
        daily_cost=Money(daily, "GBP"),
        _details=(CostGroup(legs=(leg,), cost=Money(daily, "GBP")),),
    )


def test_merge_formula_freezes_calculating_inputs():
    from dag.user_input_node import UserInputNode

    c = UserInputNode("mf_c", Commute)
    f = UserInputNode("mf_f", Commute)
    c.push(_commute("5.50"), "test")
    f.push(_commute("2.00"), "test")
    m = MergeRailFareNode("mf_m", commute_result=c, rail_fare_result=f)
    flush_all()
    assert m.latest_attempt().succeeded
    first = m.provenance_formula
    assert first is not None
    first_value = m.latest_attempt().value_or_none()
    assert first_value is not None
    assert first.result == str(first_value.daily_cost)
    # The deps move on — the formula must still show the calculating inputs.
    c.push(_commute("9.99"), "test")
    f.push(_commute("9.99"), "test")
    flush_all()
    # The merge correctly refreshes to the new inputs (dep change is
    # real) — but the OLD row's formula must still show the OLD inputs.
    # Read the old row's persisted provenance directly: it froze at
    # persist time and cannot re-read.
    from dag import persistence as persistence

    rows = (
        persistence._get_db()
        .execute("SELECT result_json FROM node_results WHERE node_id=? ORDER BY created_at", ("mf_m",))
        .fetchall()
    )
    assert len(rows) == 2, f"expected two merge rows, got {len(rows)}"
    import json
    import zlib

    first_row = json.loads(zlib.decompress(rows[0][0]).decode())
    second_row = json.loads(zlib.decompress(rows[1][0]).decode())
    assert "9.99" not in json.dumps(first_row["provenance"]), "old row re-read live deps"
    assert "9.99" in json.dumps(second_row["provenance"]), "new row must show new inputs"
    second = m.provenance_formula
    assert second is not None
    assert "9.99" in str([(line.label, line.value) for line in second.lines]), second.lines
