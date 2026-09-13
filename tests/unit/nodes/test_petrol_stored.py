"""Failing first: petrol formula shows calculating inputs, never live re-reads."""

from __future__ import annotations

import json
import zlib

from money import Money
from pint import Quantity

from dag.user_input_node import UserInputNode
from houses.commute import CostGroup, JourneyLeg, LegMode
from houses.model.domain import Commute, Person, PlaceOfInterest
from houses.nodes.petrol import PetrolCostAugmentNode
from tests.unit.conftest import flush_all


def _drive_commute() -> Commute:
    office = PlaceOfInterest("Office", "SW1P 1AA")
    leg = JourneyLeg(mode=LegMode.DRIVE, duration=Quantity(20, "minute"))
    return Commute(
        person=Person("Simon", True, places_of_interest=(office,)),
        label=office.label,
        destination=office,
        duration=Quantity(20, "minute"),
        daily_cost=Money("5.50", "GBP"),
        mode="drive",
        _details=(CostGroup(legs=(leg,)),),
    )


def test_petrol_formula_freezes_calculating_inputs():
    c = UserInputNode("pf_c", Commute)
    m = UserInputNode("pf_m", int)
    p = UserInputNode("pf_p", float)
    c.push(_drive_commute(), "test")
    m.push(45, "test")
    p.push(1.45, "test")
    n = PetrolCostAugmentNode("pf_n", commute_node=c, petrol_mpg_node=m, petrol_cost_per_litre_node=p, is_child=False)
    flush_all()
    assert n.latest_attempt().succeeded
    m.push(30, "test")
    p.push(2.00, "test")
    flush_all()
    from dag import persistence as persistence

    rows = (
        persistence._get_db()
        .execute("SELECT result_json FROM node_results WHERE node_id=? ORDER BY created_at", ("pf_n",))
        .fetchall()
    )
    assert len(rows) == 2, f"expected two rows, got {len(rows)}"
    first_row = json.loads(zlib.decompress(rows[0][0]).decode())
    second_row = json.loads(zlib.decompress(rows[1][0]).decode())
    assert "1.45" in json.dumps(first_row["provenance"]), "old row must show its calculating price"
    assert "30 mpg" not in json.dumps(first_row["provenance"]), "old row re-read live deps"
    assert "30 mpg" in json.dumps(second_row["provenance"]), "new row must show new inputs"
