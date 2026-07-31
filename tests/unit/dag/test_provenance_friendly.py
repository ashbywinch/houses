"""Provenance must be user-friendly: no node-id/dep chains, no repr dumps,
no internal source labels ('db', 'migration')."""
from __future__ import annotations

import asyncio
import json

import pytest
from money import Money

from dag.attempt import Attempt, AttemptError, Provenance
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

    def test_complex_value_omitted_not_dumped(self):
        """Person lists must not stringify into repr dumps in provenance."""
        node = UserInputNode("persons", list)

        class _Person:
            def __init__(self, name):
                self.name = name

            def __repr__(self):
                return f"Person(name='{self.name}', has_car=True, ...)"

        # Set the value directly (push() serialises through TypeAdapter,
        # which rejects non-pydantic objects) to exercise the provenance path.
        node._value = [_Person("Simon")]
        p = asyncio.run(self._provenance_of(node))
        d = p.to_dict()
        # Either the value is omitted entirely, or it's a friendly shape —
        # never the raw repr with Person(name=...
        assert "Person(name=" not in json.dumps(d)
        assert "has_car" not in json.dumps(d)

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

            async def attempt(self) -> Attempt[str]:
                return self._attempt

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

            async def attempt(self) -> Attempt[str]:
                return self._attempt

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
        from houses.nodes.life_insurance_node import LifeInsuranceTotalNode
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
