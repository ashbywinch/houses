"""Provenance must be user-friendly: no node-id/dep chains, no repr dumps,
no internal source labels ('db', 'migration')."""
from __future__ import annotations

import asyncio
import json
from typing import override

import pytest
from money import Money
from pint import Quantity

from dag.attempt import Attempt, AttemptError, Provenance, project_value
from dag.node import Node
from dag.user_input_node import UserInputNode


class TestStructuredErrorRoundTrip:
    """The structured error must survive persistence so display_message
    resolves to the friendly leaf reason after a restart."""

    def test_from_dict_round_trips_causes(self):
        leaf = AttemptError(code="no_data", message="Works estimate required for: Ashby")
        chain = AttemptError(
            code="dep_failed",
            message="89306649/total: dep failed (Works estimate required for: Ashby)",
            user_message="Works estimate required for: Ashby",
            causes=(leaf,),
        )
        d = chain.to_dict()
        # JSON-safe projection
        json.dumps(d)
        restored = AttemptError.from_dict(d)
        assert restored.code == "dep_failed"
        assert restored.display_message == "Works estimate required for: Ashby"
        assert "dep failed" in restored.message
        assert restored.causes[0].display_message == "Works estimate required for: Ashby"

    def test_display_message_is_leaf_not_chain(self):
        leaf = AttemptError(code="no_data", message="Works estimate required for: Ashby")
        chain = AttemptError(
            code="dep_failed",
            message="89306649/total: dep failed (Works estimate required for: Ashby)",
            causes=(leaf,),
        )
        assert chain.display_message == "Works estimate required for: Ashby"
        assert "89306649" not in chain.display_message
        assert "dep failed" not in chain.display_message


class TestUserInputNodeProvenance:
    """Source labels and values must be user-friendly."""

    async def _provenance_of(self, node: UserInputNode) -> Provenance:
        return await node.build_provenance()

    def test_settings_node_label_uses_setting_name(self):
        node = UserInputNode("settings/mortgage_rate", int)
        node.push(1, "db")
        p = asyncio.run(self._provenance_of(node))
        assert p.label == "Mortgage Rate"

    def test_settings_node_label_config_source(self):
        node = UserInputNode("settings/sinking_fund_rate", int)
        node.push(1, "config")
        p = asyncio.run(self._provenance_of(node))
        assert p.label == "Sinking Fund Rate"

    def test_settings_node_label_migration_source(self):
        node = UserInputNode("settings/petrol_mpg", int)
        node.push(1, "migration")
        p = asyncio.run(self._provenance_of(node))
        assert p.label == "Petrol MPG"

    def test_unknown_settings_stem_falls_back_to_title_case(self):
        node = UserInputNode("settings/new_future_setting", int)
        node.push(1, "db")
        p = asyncio.run(self._provenance_of(node))
        assert p.label == "New Future Setting"

    def test_persons_node_label(self):
        node = UserInputNode("persons", list)
        node.push([], "")
        p = asyncio.run(self._provenance_of(node))
        assert p.label == "Household members"

    def test_user_facing_label_preserved(self):
        node = UserInputNode("12345/rightmove_price", Money)
        node.push(Money("800000", "GBP"), "Rightmove")
        p = asyncio.run(self._provenance_of(node))
        assert p.label == "Rightmove"

    def test_complex_value_projected_not_dumped(self):
        """Person lists must not stringify into repr dumps in provenance —
        they project through to_provenance_value() instead."""
        from houses.model.domain import Person

        node = UserInputNode("persons", list)
        # Set the value directly (push() serialises through TypeAdapter,
        # which rejects non-pydantic objects) to exercise the provenance path.
        node._value = [Person(name="Simon", has_car=True)]
        p = asyncio.run(self._provenance_of(node))
        d = p.to_dict()
        assert "Person(name=" not in json.dumps(d)
        assert json.loads(json.dumps(d))["value"] == [
            {"name": "Simon", "has_car": True, "is_child": False, "places": []}
        ]

    def test_opaque_value_fails_fast_not_omitted(self):
        """A value with no projection raises instead of being silently
        dropped or repr-dumped."""
        node = UserInputNode("opaque", list)

        class _Opaque:
            def __repr__(self):
                return "Opaque(secrets)"

        node._value = [_Opaque()]
        with pytest.raises(TypeError, match="no provenance projection"):
            asyncio.run(self._provenance_of(node))

    def test_json_safe_value_preserved(self):
        node = UserInputNode("12345/price", Money)
        node.push(Money("800000", "GBP"), "Rightmove")
        p = asyncio.run(self._provenance_of(node))
        # Money serialises as its string form in provenance
        assert p.to_dict()["value"] == "GBP 800,000.00"


class TestLoadReconstructsStructuredError:
    """_load_attempt_from_db must rebuild error_info from persisted
    error_detail so the friendly message survives restarts."""

    def test_impossible_with_error_detail_reconstructed(self):
        from dag.persistence import save_node_result

        leaf = AttemptError(code="no_data", message="Works estimate required for: Ashby")
        detail = AttemptError(
            code="dep_failed",
            message="89306649/total: dep failed (Works estimate required for: Ashby)",
            user_message="Works estimate required for: Ashby",
            causes=(leaf,),
        ).to_dict()
        save_node_result(
            "test_load_recon/x",
            {
                "status": "impossible",
                "value": None,
                "error": "89306649/total: dep failed (Works estimate required for: Ashby)",
                "error_detail": detail,
            },
            {},
        )

        class _Node(Node[str]):
            _attempt: Attempt[str]

            def __init__(self):
                super().__init__("test_load_recon/x", str)

            @override
            async def attempt(self) -> Attempt[str]:
                return self._attempt

            @override
            async def build_provenance(self):
                return Provenance(label="x")

        n = _Node()
        a = n._load_attempt_from_db()
        assert a is not None and a.impossible
        info = a.error_info
        assert info is not None
        assert info.display_message == "Works estimate required for: Ashby"
        assert "dep failed" not in info.display_message

    def test_legacy_row_without_detail_falls_back(self):
        from dag.persistence import save_node_result

        save_node_result(
            "test_load_recon/y",
            {"status": "impossible", "value": None, "error": "89306649/y: dep failed (raw)"},
            {},
        )

        class _Node(Node[str]):
            _attempt: Attempt[str]

            def __init__(self):
                super().__init__("test_load_recon/y", str)

            @override
            async def attempt(self) -> Attempt[str]:
                return self._attempt

            @override
            async def build_provenance(self):
                return Provenance(label="y")

        n = _Node()
        a = n._load_attempt_from_db()
        assert a is not None and a.impossible
        # No structured info to recover — error_info is the no_data fallback
        assert a.error_info is not None
        assert a.error_info.code == "no_data"


class TestLifeInsurancePerPerson:
    """Life insurance provenance must name each person, not a single
    'Life Insurance Total' line (per the prototype's per-person rows)."""

    @pytest.mark.asyncio
    async def test_formula_lists_each_person(self):
        from houses.model.domain import Person
        from houses.nodes.life_insurance_total_node import LifeInsuranceTotalNode
        from tests.helpers import make_services

        svc = make_services()
        node = LifeInsuranceTotalNode("t/li", persons_source=svc.persons_source)
        svc.persons_source.push(
            [
                Person(name="Simon", has_car=True, life_insurance_monthly=Money("150", "GBP")),
                Person(name="Lorena", has_car=False, life_insurance_monthly=Money("0", "GBP")),
                Person(name="Ashby", has_car=True, life_insurance_monthly=Money("0", "GBP")),
            ],
            "test",
        )
        from dag.scheduler import flush_processor

        await flush_processor()
        a = await node.attempt()
        assert a.succeeded, f"life insurance failed: {a.error}"
        formula = node.provenance_formula
        assert formula is not None
        labels = [line.label for line in formula.lines]
        assert "Simon’s life insurance" in labels, f"got: {labels}"
        assert "Lorena’s life insurance" in labels
        assert "Ashby’s life insurance" in labels
        assert "Life Insurance Total" not in labels
        # Values reflect each person
        values = {line.label: line.value for line in formula.lines}
        assert values["Simon’s life insurance"] == "£150.00"
        assert values["Lorena’s life insurance"] == "£0.00"


class TestPerPropertyNodeLabels:
    """Per-property source nodes (e.g. 87650634/status) must show a
    friendly name, not the node id or internal source label."""

    def test_status_node_label(self):
        node = UserInputNode("87650634/status", str)
        node.push("Current", "sheet-migration")
        p = asyncio.run(node.build_provenance())
        assert p.label == "Property Status"

    def test_works_estimates_label(self):
        node = UserInputNode("87650634/works_estimates", dict)
        node.push({"Ashby": 20000}, "sheet-migration")
        p = asyncio.run(node.build_provenance())
        assert p.label == "Renovation estimates"

    def test_unknown_stem_falls_back_to_title_case(self):
        node = UserInputNode("87650634/some_new_node", str)
        node.push("x", "sheet-migration")
        p = asyncio.run(node.build_provenance())
        assert p.label == "Some New Node"

    def test_works_estimates_value_is_friendly_dict(self):
        """Per-person estimates stay as a dict (frontend formats it),\n        never a repr dump."""
        node = UserInputNode("87650634/works_estimates", dict)
        node.push({"Ashby": 20000, "Simon": 15000}, "sheet-migration")
        p = asyncio.run(node.build_provenance())
        assert p.to_dict()["value"] == {"Ashby": 20000, "Simon": 15000}


class TestTotalWorksPerPerson:
    """Total works provenance must list each person's estimate via the
    generic formula, matching the prototype's per-person rows."""

    @pytest.mark.asyncio
    async def test_formula_lists_each_person(self):
        from houses.model.domain import Person
        from houses.nodes.total_works_node import TotalWorksNode

        persons = UserInputNode("t/we_ps", list)
        works = UserInputNode("t/we_ws", dict[str, Money])
        node = TotalWorksNode("t/we", persons_source=persons, works_estimates_node=works)
        persons.push(
            [
                Person(name="Ashby", has_car=True, works_estimate_required=False),
                Person(name="Simon", has_car=True, works_estimate_required=False),
            ],
            "test",
        )
        works.push({"Ashby": Money("20000", "GBP"), "Simon": Money("5000", "GBP")}, "user")
        from dag.scheduler import flush_processor

        await flush_processor()
        a = await node.attempt()
        assert a.succeeded, f"total works failed: {a.error}"
        formula = node.provenance_formula
        assert formula is not None
        labels = [line.label for line in formula.lines]
        assert "Ashby’s renovation estimate" in labels, f"got: {labels}"
        assert "Simon’s renovation estimate" in labels
        values = {line.label: line.value for line in formula.lines}
        assert values["Ashby’s renovation estimate"] == "£20,000.00"
        assert values["Simon’s renovation estimate"] == "£5,000.00"

    def test_typed_node_round_trips_money(self):
        """dict[str, Money] reloads Money objects, not raw numbers."""
        node = UserInputNode("t/we2", dict[str, Money])
        node.push({"Ashby": Money("20000", "GBP")}, "test")
        from dag.persistence import latest_node_result

        row = latest_node_result("t/we2")
        assert row is not None
        assert row["value"] == {"Ashby": {"amount": "20000.00", "currency": "GBP"}}
        n2 = UserInputNode("t/we2", dict[str, Money])
        loaded = n2._load_attempt_from_db()
        assert loaded is not None
        val = loaded.value_or_none()
        assert val is not None
        assert isinstance(val["Ashby"], Money)
        assert val["Ashby"] == Money("20000", "GBP")


class TestValueProjection:
    """Provenance values are projected to JSON-safe form — never repr-dumped,
    never silently dropped, and unprojectable values fail fast."""

    def test_commute_object_projected_not_dumped(self):
        from houses.model.domain import Commute, Person, PlaceOfInterest

        commute = Commute(
            person=Person(name="", has_car=True),
            label="Pimlico",
            destination=PlaceOfInterest(label="Pimlico", address="SW1V 2QQ"),
            duration=Quantity(68, "minute"),  # type: ignore[arg-type]  # pint's stub types Quantity(68, "minute") as PlainQuantity, not assignable to Commute.duration's bare Quantity[Unknown] (invariant generic); PlainQuantity is a pint Quantity at runtime
            daily_cost=Money("27", "GBP"),
        )
        # The node path projects before constructing Provenance.
        prov = Provenance(label="Commute", value=project_value(commute))
        d = prov.to_dict()
        assert "Commute(person=" not in json.dumps(d)
        assert d["value"] == "Transit · 68 min · £27.00/day to Pimlico · 1x/wk · 46 wks/yr"

    def test_person_list_projected(self):
        from houses.model.domain import Person

        prov = Provenance(label="Persons", value=project_value([Person(name="Simon", has_car=True)]))
        d = prov.to_dict()
        assert json.loads(json.dumps(d))["value"] == [
            {"name": "Simon", "has_car": True, "is_child": False, "places": []}
        ]

    def test_money_dict_projected(self):
        prov = Provenance(label="Works", value=project_value({"Ashby": Money("20000", "GBP")}))
        d = prov.to_dict()
        assert json.loads(json.dumps(d))["value"] == {"Ashby": "GBP 20,000.00"}

    def test_council_tax_projected_human_not_machine_dict(self):
        """Council tax provenance must read as a human summary — the old
        projection dumped {"band", "yearly_cost": "GBP 2,500.00",
        "uncertainty", "evidence_url"} into the detail tree."""
        from dag.measurement import Measurement
        from houses.council_tax_info import CouncilTaxInfo

        info = CouncilTaxInfo(
            band="D",
            yearly_cost=Measurement(Money("2500", "GBP"), 0.0),
            evidence_url="https://gov.uk/council-tax-bands",
        )
        prov = Provenance(label="Council Tax", value=project_value(info))
        d = prov.to_dict()
        assert d["value"] == "Band D · £2,500.00/yr"
        assert "evidence_url" not in d["value"] and "uncertainty" not in d["value"]

    def test_unprojectable_value_fails_fast(self):
        class Opaque:
            pass

        with pytest.raises(TypeError, match="no provenance projection"):
            project_value(Opaque())

    def test_unprojected_value_rejected_at_serialization(self):
        """A value that skips projection must not silently degrade."""

        class Opaque:
            pass

        prov = Provenance(label="Bad", value=Opaque())
        with pytest.raises(TypeError, match="not JSON-serializable"):
            prov.to_dict()

    def test_build_provenance_applies_projection(self):
        from houses.model.domain import Commute, Person, PlaceOfInterest

        commute = Commute(
            person=Person(name="", has_car=True),
            label="Pimlico",
            destination=PlaceOfInterest(label="Pimlico", address="SW1V 2QQ"),
            duration=Quantity(68, "minute"),  # type: ignore[arg-type]  # pint's stub types Quantity(68, "minute") as PlainQuantity, not assignable to Commute.duration's bare Quantity[Unknown] (invariant generic); PlainQuantity is a pint Quantity at runtime
            daily_cost=Money("27", "GBP"),
        )
        node = UserInputNode("proj_test", Commute)
        node._value = commute
        p = asyncio.run(node.build_provenance())
        assert json.loads(json.dumps(p.to_dict()))["value"] == (
            "Transit · 68 min · £27.00/day to Pimlico · 1x/wk · 46 wks/yr"
        )


class TestEmptySourceLabel:
    """Nodes with an empty source label must still get a friendly name,
    not the raw node id."""

    def test_works_estimates_empty_label(self):
        node = UserInputNode("87974082/works_estimates", dict[str, Money])
        node._value = {}
        node._source_label = ""
        p = asyncio.run(node.build_provenance())
        assert p.label == "Renovation estimates"
        assert "87974082" not in p.label


class TestDecimalProjection:
    """Decimal settings (petrol cost, mortgage rate) are value types with a
    canonical JSON projection — provenance must never raise on them."""

    def test_decimal_projects_to_float(self):
        from decimal import Decimal

        prov = Provenance(label="Petrol", value=project_value(Decimal("1.45")))
        d = prov.to_dict()
        assert json.loads(json.dumps(d))["value"] == 1.45

    @pytest.mark.asyncio
    async def test_decimal_setting_builds_provenance(self):
        from decimal import Decimal

        node = UserInputNode("settings/petrol_cost_per_litre", Decimal)
        node._value = Decimal("1.45")
        p = await node.build_provenance()
        assert json.loads(json.dumps(p.to_dict()))["value"] == 1.45
