"""Dynamic dependency sets: a node's deps may be supplied as a zero-arg
provider callable (composition) instead of a static tuple.

The provider closes over its own data and may be evaluated at any point
in the node's life — construction, staleness checks, refresh.  It must
never read `self`; the owner of the underlying data schedules the node
when that data changes."""

from __future__ import annotations

from decimal import Decimal
from typing import override

from money import Money
from pint import Quantity

import dag.user_input_node  # noqa: F401 — register pydantic schemas
from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.node import Node
from dag.scheduler import get_scheduler
from dag.user_input_node import UserInputNode
from houses.model.domain import Commute, Person, PlaceOfInterest
from houses.nodes.commute_breakdown_node import CommuteBreakdownNode
from tests.helpers import FixedCommuteNode
from tests.unit.conftest import flush_all

_WEEKS_PER_YEAR = 46


def test_deps_provider_is_not_evaluated_during_construction():
    calls: list[int] = []
    dep = UserInputNode("dyn_dep", int)
    dep.push(7, "test")

    class _PassThrough(DerivedNode[int]):
        @staticmethod
        @override
        def compute(*dep_attempts: Attempt) -> Attempt[int]:
            (dep_attempt,) = dep_attempts
            return Attempt.succeeded(dep_attempt.value_or_none())

    def provider():
        calls.append(1)
        return (dep,)

    node = _PassThrough("dyn_node", int, provider)
    assert calls == [], "the provider must not run inside __init__"
    assert node._get_active_deps() == (dep,)
    get_scheduler().unregister(node)


def test_breakdown_construction_with_zero_post_super_attributes():
    """The congestion-gate composition: CommuteBreakdownNode passes its
    dep policy as a closure over its CONSTRUCTOR ARGUMENTS, so the node
    is fully registered and staleness-checkable immediately after
    construction — no post-super attribute reads can crash it (the live
    startup crash on 2026-09-09)."""
    selectors: dict[str, Node] = {}
    persons = UserInputNode("cb_persons", list)
    node = CommuteBreakdownNode(
        "cb_breakdown",
        commute_selectors=selectors,
        persons_source=persons,
    )
    # Immediately staleness-checkable (this is what register() does):
    assert node._is_stale() in (True, False)

    # The provider reads the LIVE dict: destinations added later become
    # deps without any rebuild.
    selectors["Simon/Pimlico"] = UserInputNode("cb_pimlico", PlaceOfInterest)
    deps_after = node._get_active_deps()
    assert any(getattr(d, "_id", "") == "cb_pimlico" for d in deps_after)


def test_breakdown_total_is_the_plain_multiplication():
    """Pimlico (zone, 0 days) contributes £0 by the multiplication;
    Bracknell contributes £18.03 × 1 × 46."""
    persons = UserInputNode("mp_persons", list)
    persons.push(
        [
            Person(
                name="Simon",
                has_car=True,
                places_of_interest=(
                    PlaceOfInterest(
                        label="Pimlico", address="SW1V 2QQ",
                        trips_per_week=0, weeks_per_year=_WEEKS_PER_YEAR,
                    ),
                    PlaceOfInterest(
                        label="Bracknell", address="RG12 8YA",
                        trips_per_week=1, weeks_per_year=_WEEKS_PER_YEAR,
                    ),
                ),
            )
        ],
        "test",
    )

    def _canned(node_id: str, label: str, daily_gbp: str) -> FixedCommuteNode:
        node = FixedCommuteNode(node_id)
        node.set(
            Commute(
                person=Person(name="Simon", has_car=True),
                label=label,
                destination=PlaceOfInterest(label=label, address=""),
                duration=Quantity(30, "minute"),
                daily_cost=Money(daily_gbp, "GBP"),
            )
        )
        return node

    pimlico = _canned(node_id="mp_pimlico", label="Pimlico", daily_gbp="18.03")
    bracknell = _canned(node_id="mp_bracknell", label="Bracknell", daily_gbp="10.00")
    node = CommuteBreakdownNode(
        "mp_breakdown",
        commute_selectors={"Simon/Pimlico": pimlico, "Simon/Bracknell": bracknell},
        persons_source=persons,
    )
    flush_all()
    get_scheduler().schedule(node)
    flush_all()
    a = node.latest_attempt()
    assert a.succeeded, a.error
    val = a.value_or_none()
    assert val is not None
    simon = val["persons"]["Simon"]
    by_label = {c["label"]: Decimal(c["yearly_gbp"]) for c in simon["commutes"]}
    assert Decimal(by_label["Pimlico"]) == Decimal("0.00"), (
        "a 0-trip destination contributes £0 by the multiplication"
    )
    assert Decimal(by_label["Bracknell"]) == Decimal("10.00") * _WEEKS_PER_YEAR
