from __future__ import annotations

from dag.attempt import Attempt, Provenance
from dag.computed_node import ComputedNode
from dag.source_node import SourceNode
from houses.model.domain import PlaceOfInterest


class TestDefaultSettings:
    def test_default_has_persons(self):
        from houses.nodes.settings import make_default_settings

        dto = make_default_settings()
        persons = dto["persons"]
        assert len(persons) >= 2
        names = [p.name for p in persons]
        assert "Simon" in names
        assert "Lorena" in names

    def test_default_settings_values(self):
        from houses.nodes.settings import make_default_settings

        dto = make_default_settings()
        assert dto["bus_walk_penalty_minutes"] == 10
        assert "Simon" in dto["commute_thresholds"]
        assert "Lorena" in dto["commute_thresholds"]

    def test_default_persons_have_places_of_interest(self):
        from houses.nodes.settings import make_default_settings

        dto = make_default_settings()
        for p in dto["persons"]:
            assert len(p.places_of_interest) > 0
            assert isinstance(p.places_of_interest[0], PlaceOfInterest)


class TestSettingsSourceNode:
    def test_holds_defaults(self):
        from houses.nodes.settings import make_settings_node

        node = make_settings_node()
        a = node.attempt()
        assert a.is_succeeded
        v = a.value_or_none()
        assert v["bus_walk_penalty_minutes"] == 10

    def test_to_json_shape(self):
        from houses.nodes.settings import make_settings_node

        node = make_settings_node()
        j = node.to_json()
        assert j["succeeded"] is True
        value = j["value"]
        assert "persons" in value
        assert "bus_walk_penalty_minutes" in value
        assert j["error"] is None

    def test_push_updates_value(self):
        from houses.nodes.settings import make_default_settings, make_settings_node

        node = make_settings_node()
        new = make_default_settings()
        new["bus_walk_penalty_minutes"] = 15
        node.push(new, Provenance("user"))

        a = node.attempt()
        assert a.value_or_none()["bus_walk_penalty_minutes"] == 15

    def test_push_emits_changed(self):
        from houses.nodes.settings import make_default_settings, make_settings_node

        node = make_settings_node()
        received = []
        node.changed.connect(lambda: received.append("changed"))

        new = make_default_settings()
        new["bus_walk_penalty_minutes"] = 15
        node.push(new, Provenance("user"))

        assert len(received) == 1

    def test_downstream_recomputes_on_push(self):
        src = SourceNode[dict]("test_setting", dict)
        src.push({"bus_walk_penalty_minutes": 10}, Provenance("default"))

        class PenaltyAwareNode(ComputedNode[int]):
            def __init__(self):
                super().__init__("penalty_aware", int, (src,))
                self.compute_count = 0

            def compute(self, *dep_attempts: Attempt) -> Attempt[int]:
                self.compute_count += 1
                s = dep_attempts[0].value_or_none() or {}
                penalty = s.get("bus_walk_penalty_minutes", 0)
                return Attempt.succeeded(penalty, Provenance("computed"))

        node = PenaltyAwareNode()

        assert node.attempt().value_or_none() == 10
        assert node.compute_count == 1

        src.push({"bus_walk_penalty_minutes": 20}, Provenance("user"))

        assert node.attempt().value_or_none() == 20
        assert node.compute_count == 2
