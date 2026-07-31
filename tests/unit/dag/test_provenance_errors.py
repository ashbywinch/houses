"""Provenance error-state serialisation tests.

The frontend renders provenance JSON directly. When a node is impossible,
the provenance must carry status="impossible" and the error message so the
UI can show an error state (matching the designer's four prototype datasets).
"""
from __future__ import annotations

import pytest

from dag.attempt import Attempt, Provenance, SourceType
from dag.derived_node import DerivedNode
from dag.user_input_node import UserInputNode


class TestProvenanceErrorSerialisation:
    def test_succeeded_provenance_has_no_status_or_error(self):
        p = Provenance(label="ok", value=42)
        d = p.to_dict()
        assert "status" not in d
        assert "error" not in d

    def test_impossible_provenance_serialises_status_and_error(self):
        p = Provenance(
            label="council_tax",
            status="impossible",
            error="Ambiguous address: 2 council tax bands found for this postcode (D and E)",
        )
        d = p.to_dict()
        assert d["status"] == "impossible"
        assert "Ambiguous address: 2 council tax bands" in d["error"]

    def test_nested_impossible_source_serialises_error(self):
        child = Provenance(
            label="transit",
            status="impossible",
            error="TfL API returned 409 Conflict",
            url="https://api.tfl.gov.uk/",
        )
        parent = Provenance(label="commute", sources={"transit": child})
        d = parent.to_dict()
        assert d["sources"]["transit"]["status"] == "impossible"
        assert d["sources"]["transit"]["error"] == "TfL API returned 409 Conflict"
        assert d["sources"]["transit"]["url"] == "https://api.tfl.gov.uk/"

    def test_error_message_carries_full_text(self):
        p = Provenance(status="impossible", error="Works estimate required for: Ashby")
        assert p.to_dict()["error"] == "Works estimate required for: Ashby"


class _ImpossibleNode(DerivedNode[int]):
    """DerivedNode whose attempt is impossible, mirroring a failed API lookup."""

    def __init__(self, node_id: str, error: str):
        super().__init__(node_id, int, deps=())
        self._error = error
        self._attempt = Attempt.impossible(error)

    async def attempt(self):
        return self._attempt

    def compute(self):
        raise AssertionError("compute should not run for a pre-set impossible attempt")

    @property
    def provenance_source_type(self) -> SourceType:
        return SourceType.API


class TestBuildProvenanceErrorState:
    @pytest.mark.asyncio
    async def test_build_provenance_marks_impossible_attempt(self):
        node = _ImpossibleNode("council_tax", "Ambiguous address: 2 bands found")
        p = await node.build_provenance()
        assert p.status == "impossible"
        assert p.error == "Ambiguous address: 2 bands found"

    @pytest.mark.asyncio
    async def test_to_json_provenance_includes_error(self):
        node = _ImpossibleNode("council_tax", "Ambiguous address: 2 bands found")
        j = await node.to_json()
        assert j["status"] == "impossible"
        assert j["provenance"]["status"] == "impossible"
        assert j["provenance"]["error"] == "Ambiguous address: 2 bands found"

    @pytest.mark.asyncio
    async def test_parent_provenance_contains_child_error(self):
        """Parent with a succeeded attempt still surfaces a failed child."""
        child = _ImpossibleNode("child/transit", "TfL API returned 409 Conflict")
        child._attempt = Attempt.impossible("TfL API returned 409 Conflict")

        class _Parent(DerivedNode[int]):
            def __init__(self):
                super().__init__("parent", int, deps=(child,))
                self._child = child

            @property
            def provenance_formula(self):
                return None

            def _get_active_deps(self):
                return (self._child,)

            def compute(self):
                return Attempt.succeeded(10)

            async def attempt(self):
                return Attempt.succeeded(10)

        parent = _Parent()
        p = await parent.build_provenance()
        assert p.sources["child/transit"].status == "impossible"
        assert p.sources["child/transit"].error == "TfL API returned 409 Conflict"

    @pytest.mark.asyncio
    async def test_user_input_node_has_no_false_error(self):
        node = UserInputNode("settings/rate", float)
        node.push(4.95, "test")
        p = await node.build_provenance()
        d = p.to_dict()
        assert "status" not in d
        assert "error" not in d
