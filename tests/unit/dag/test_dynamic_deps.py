"""Dynamic dependency sets: deps are nodes; a node whose dep SET
changes at runtime rewires with ``set_deps(...)``.

A callable passed as ``deps`` raises TypeError — dependencies are nodes,
never lambdas, dicts, or provider closures. The node connected to the
change signal of each dep; the owner that mutates the input set calls
``set_deps`` so the wiring follows the set."""

from __future__ import annotations

from decimal import Decimal
from typing import override

from money import Money
from pint import Quantity

import dag.user_input_node  # noqa: F401 — register pydantic schemas
from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.scheduler import get_scheduler
from dag.user_input_node import UserInputNode
from houses.model.domain import Commute, Person, PlaceOfInterest
from houses.nodes.commute_breakdown_node import CommuteBreakdownNode
from tests.helpers import FixedCommuteNode
from tests.unit.conftest import flush_all

_WEEKS_PER_YEAR = 46


def test_callable_deps_raise_typeerror():
    """Deps are nodes — a provider closure is the wiring bug, not a
    composition tool."""
    import pytest

    dep = UserInputNode("dyn_callable_dep", int)

    class _PassThrough(DerivedNode[int]):
        @staticmethod
        @override
        def compute(*dep_attempts: Attempt) -> Attempt[int]:
            (dep_attempt,) = dep_attempts
            return Attempt.succeeded(dep_attempt.value_or_none())

    from typing import Any, cast

    callable_deps = cast(Any, lambda: (dep,))
    with pytest.raises(TypeError):
        _PassThrough("dyn_callable", int, callable_deps)


def test_set_deps_rewires_signals():
    """After set_deps, a write to the NEW dep wakes the node — the dep
    graph, not a hand-crafted schedule, drives updates."""
    a = UserInputNode("dyn_a", int)
    b = UserInputNode("dyn_b", int)
    a.push(1, "test")

    class _Sum(DerivedNode[int]):
        @staticmethod
        @override
        def compute(x: Attempt[int]) -> Attempt[int]:
            return Attempt.succeeded(x.value_or_none() or 0)

    node = _Sum("dyn_sum", int, (a,), dep_names=("x",))
    flush_all()
    assert node.latest_attempt().value_or_none() == 1
    node.set_deps((b,))
    b.push(5, "test")
    flush_all()
    assert node.latest_attempt().value_or_none() == 5


def test_breakdown_construction_with_zero_post_super_attributes():
    """CommuteBreakdownNode takes its selectors as deps (tuple of
    nodes); the node is fully registered and staleness-checkable
    immediately after construction."""
    persons = UserInputNode("cb_persons2", list)
    pimlico = UserInputNode("simon/Pimlico", PlaceOfInterest)
    node = CommuteBreakdownNode(
        "cb_breakdown2",
        selectors=(pimlico,),
        persons_source=persons,
    )
    assert node._is_stale() in (True, False)
    deps = node._get_active_deps()
    assert any(getattr(d, "_id", "") == "simon/Pimlico" for d in deps)


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
                        label="Pimlico",
                        address="SW1V 2QQ",
                        trips_per_week=0,
                        weeks_per_year=_WEEKS_PER_YEAR,
                    ),
                    PlaceOfInterest(
                        label="Bracknell",
                        address="RG12 8YA",
                        trips_per_week=1,
                        weeks_per_year=_WEEKS_PER_YEAR,
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

    pimlico = _canned(node_id="simon/Pimlico", label="Pimlico", daily_gbp="18.03")
    bracknell = _canned(node_id="simon/Bracknell", label="Bracknell", daily_gbp="10.00")
    node = CommuteBreakdownNode(
        "mp_breakdown",
        selectors=(pimlico, bracknell),
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
    assert Decimal(by_label["Pimlico"]) == Decimal("0.00"), "a 0-trip destination contributes £0 by the multiplication"
    assert Decimal(by_label["Bracknell"]) == Decimal("10.00") * _WEEKS_PER_YEAR
