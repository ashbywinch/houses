from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic_core import core_schema

from dag.attempt import Attempt, Provenance
from dag.node import Node

# Register pydantic schemas for third-party types (Money, Quantity) so
# TypeAdapter can handle them automatically.  This IS the correct
# pydantic v2 approach — __get_pydantic_core_schema__ is an explicit
# protocol they support.
try:
    from money import Money
    from pint import Quantity
    from pydantic_core import core_schema

    if not hasattr(Money, "__get_pydantic_core_schema__"):
        def _money_schema(_source, _handler):
            def validate(v):
                if isinstance(v, Money):
                    return v
                if isinstance(v, dict):
                    return Money(v.get("amount", 0), v.get("currency", "GBP"))
                raise ValueError(f"Cannot convert {type(v)} to Money")

            def serialize(m):
                return {"amount": float(m.amount), "currency": m.currency}

            return core_schema.no_info_plain_validator_function(
                validate,
                serialization=core_schema.plain_serializer_function_ser_schema(serialize),
            )

        Money.__get_pydantic_core_schema__ = _money_schema

    if not hasattr(Quantity, "__get_pydantic_core_schema__"):
        def _quantity_schema(_source, _handler):
            def validate(v):
                if isinstance(v, Quantity):
                    return v
                if isinstance(v, dict):
                    return Quantity(v.get("value", 0), v.get("unit", ""))
                raise ValueError(f"Cannot convert {type(v)} to Quantity")

            def serialize(q):
                m = float(q.magnitude)
                return {"value": int(m) if m == int(m) else m, "unit": str(q.units)}

            return core_schema.no_info_plain_validator_function(
                validate,
                serialization=core_schema.plain_serializer_function_ser_schema(serialize),
            )

        Quantity.__get_pydantic_core_schema__ = _quantity_schema

except ImportError:
    pass


T = TypeVar("T")


class UserInputNode(Node[T], Generic[T]):
    """A leaf node whose value is set externally by enrichment modules,
    sheet imports, or user edits.

    Call ``.push(value, source_label)`` to set a new value. This emits the
    ``changed`` signal so that downstream DerivedNodes re-compute.
    Persists to SQLite automatically on every push.
    """

    def __init__(self, node_id: str, value_type: type[T]) -> None:
        super().__init__(node_id, value_type)
        self._value: T | None = None
        self._source_label: str = ""
        loaded = self._load_attempt_from_db()
        if loaded is not None and loaded.succeeded:
            self._value = loaded.value_or_none()
            self._source_label = self._load_persisted_label()

    def _load_persisted_label(self) -> str:
        from dag.persistence import latest_node_result
        result = latest_node_result(self._id)
        if result is not None:
            return result.get("source_label", "")
        return ""
    def push(self, value: T, source_label: str = "") -> None:
        """Set a new value and persist.

        Args:
            value: The value to store. Validated through the type adapter
                so Person dataclasses and other structured types work.
            source_label: Human-readable source identifier
                (e.g. ``"Rightmove"``, ``"User correction"``, ``"TfL API"``).
        """
        self._value = self._adapter.validate_python(value)
        self._source_label = source_label
        result_dict: dict[str, Any] = {
            "status": "succeeded",
            "value": self._adapter.dump_python(self._value),
            "source_label": source_label,
        }
        self._persist(result_dict)
        self.changed.emit()

    async def attempt(self) -> Attempt[T]:
        if self._value is not None:
            return Attempt.succeeded(self._value)
        return Attempt.pending()

    async def build_provenance(self) -> Provenance:
        return Provenance(label=self._source_label, value=self._value)

    def latest_attempt(self) -> Attempt:
        if self._value is not None:
            return Attempt.succeeded(self._value)
        return Attempt.pending()
