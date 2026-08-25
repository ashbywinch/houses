"""A3 — council-tax fallback with a spread, and the total inheriting "≈".

On lookup failure the CouncilTaxNode returns a Band D estimate with a
standard deviation instead of plain "?"; TotalMonthlyHousingCostNode
inherits the uncertainty (exact = zero spread). Regression tests for
the node and the total.
"""

from __future__ import annotations

import pytest
from money import Money

from dag.attempt import Attempt
from dag.expression import Add, Literal, Ref
from dag.measurement import Measurement
from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode
from houses.council_tax_info import CouncilTaxInfo
from houses.nodes.epc_node import CouncilTaxNode
from houses.services_provider import _request_services as _sp
from tests.helpers import make_services

BAND_D_ESTIMATE = Money("1200", "GBP")
BAND_D_SPREAD = 50.0


class _FailingCT:
    async def lookup(self, postcode, address=""):
        return Attempt.impossible("address matched multiple properties")


class _ExactCT:
    async def lookup(self, postcode, address=""):
        return Attempt.succeeded(
            CouncilTaxInfo(band="D", yearly_cost=Measurement(Money("1800", "GBP"), 0.0))
        )


def _council_tax_node(svc):
    token = _sp.set(make_services(council_tax_service=svc))
    addr = UserInputNode[str]("addr_a3", str)
    pc = UserInputNode[str]("pc_a3", str)
    node = CouncilTaxNode("ct_a3", best_address=addr, postcode_node=pc)
    addr.push("1 High Street, Egham, TW20 9JP", "test")
    pc.push("TW20 9JP", "test")
    return node, token


@pytest.mark.asyncio
async def test_council_tax_fallback_returns_band_d_estimate_with_spread():
    node, token = _council_tax_node(_FailingCT())
    try:
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        info = a.value_or_none()
        assert info is not None
        assert info.band == "?"
        assert info.yearly_cost == Measurement(BAND_D_ESTIMATE, BAND_D_SPREAD)
    finally:
        _sp.reset(token)


@pytest.mark.asyncio
async def test_council_tax_success_wraps_exact_measurement():
    node, token = _council_tax_node(_ExactCT())
    try:
        await flush_processor()
        a = await node.attempt()
        assert a.succeeded
        info = a.value_or_none()
        assert info is not None
        assert info.band == "D"
        assert info.yearly_cost == Measurement(Money("1800", "GBP"), 0.0)
    finally:
        _sp.reset(token)


@pytest.mark.asyncio
async def test_council_tax_fallback_provenance_notes_estimation():
    node, token = _council_tax_node(_FailingCT())
    try:
        await flush_processor()
        p = await node.build_provenance()
        assert p.description is not None
        assert "estimated" in p.description.lower()
    finally:
        _sp.reset(token)


def _total_node(council_info: CouncilTaxInfo):
    from houses.nodes.total_monthly_housing_cost_node import HousingCostConfig, TotalMonthlyHousingCostNode

    mg = UserInputNode[Money]("a3_mg", Money)
    sf = UserInputNode[Money]("a3_sf", Money)
    li = UserInputNode[Money]("a3_li", Money)
    ri = UserInputNode[Money]("a3_ri", Money)
    st = UserInputNode[str]("a3_st", str)
    cb = UserInputNode[dict]("a3_cb", dict)
    ct = UserInputNode[CouncilTaxInfo]("a3_ct", CouncilTaxInfo)

    node = TotalMonthlyHousingCostNode(
        "a3_tmc",
        config=HousingCostConfig(
            monthly_mortgage_node=mg,
            yearly_sinking_fund_node=sf,
            life_insurance_node=li,
            rental_income_node=ri,
            status_node=st,
            commute_breakdown_node=cb,
            council_tax_node=ct,
        ),
    )
    mg.push(Money("1000", "GBP"), "test")
    sf.push(Money("0", "GBP"), "test")
    li.push(Money("0", "GBP"), "test")
    ri.push(Money("0", "GBP"), "test")
    st.push("Sold", "test")
    cb.push({}, "test")
    ct.push(council_info, "test")
    return node


@pytest.mark.asyncio
async def test_total_monthly_inherits_council_tax_uncertainty():
    node = _total_node(
        CouncilTaxInfo(band="?", yearly_cost=Measurement(BAND_D_ESTIMATE, BAND_D_SPREAD))
    )
    await flush_processor()
    a = await node.attempt()
    assert a.succeeded
    total = a.value_or_none()
    assert total is not None
    # 1000 mortgage + (1200 ± 50)/12 council = 1100 ± 4.1666…
    assert total.value == Money("1100", "GBP")
    assert total.stddev == pytest.approx(BAND_D_SPREAD / 12)


@pytest.mark.asyncio
async def test_total_monthly_exact_when_all_components_exact():
    node = _total_node(
        CouncilTaxInfo(band="D", yearly_cost=Measurement(Money("1800", "GBP"), 0.0))
    )
    await flush_processor()
    a = await node.attempt()
    assert a.succeeded
    total = a.value_or_none()
    assert total is not None
    assert total.value == Money("1150", "GBP")
    assert total.stddev == 0.0


def test_add_expression_accepts_money_plus_measurement():
    """Add must fall back to the reversed operand order — Money.__add__
    raises TypeError instead of returning NotImplemented, so without the
    fallback a measured term entering the sum would bail."""
    m = Measurement(Money("1200", "GBP"), 50.0)
    expr = Add(Literal(Money("1000", "GBP")), Ref(_Node(m)))
    result = expr.evaluate()
    assert result.succeeded
    assert result.value is not None
    assert result.value.value == Money("2200", "GBP")
    assert result.value.stddev == pytest.approx(50.0)


class _Node:
    """Minimal node for expression tests."""

    _id = "a3_node"
    display_name = "a3_node"

    def __init__(self, value):
        self._attempt = Attempt.succeeded(value)

    def latest_attempt(self):
        return self._attempt
