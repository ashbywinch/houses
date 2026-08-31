"""Leaf node whose value is set externally."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal as _Decimal
from typing import Any, Generic, TypeVar, cast, override

from money import Money
from pint import Quantity
from pydantic_core import core_schema

import dag.persistence as _per
from dag.attempt import Attempt, Provenance, SourceType, project_value
from dag.eval_context import staged_attempt
from dag.node import Node
from dag.persistence import latest_node_result

logger = logging.getLogger(__name__)

# Friendly display names for settings nodes, keyed by the node id stem
# (the part after "settings/"). Matches the prototype's labels.
_SETTING_LABELS: dict[str, str] = {
    "mortgage_rate": "Mortgage Rate",
    "mortgage_term": "Mortgage Term (years)",
    "sinking_fund_rate": "Sinking Fund Rate",
    "life_insurance_monthly": "Life Insurance Monthly",
    "working_weeks": "Working Weeks per Year",
    "current_home_sale_price": "Current Home Sale Price",
    "current_home_outstanding_mortgage": "Current Home Outstanding Mortgage",
    "petrol_mpg": "Petrol MPG",
    "petrol_cost_per_litre": "Petrol Cost per Litre",
    "rental_income_monthly": "Rental Income Monthly",
}

# Friendly names for per-property source node stems (e.g. the last path
# segment of "87650634/status").
_NODE_STEM_LABELS: dict[str, str] = {
    "status": "Property Status",
    "works_estimates": "Renovation estimates",
    "rightmove_price": "Rightmove",
    "rightmove_address": "Address from Rightmove",
    "rightmove_bedrooms": "Bedrooms from Rightmove",
    "best_address": "Property address",
    "best_location": "Property location",
    "postcode": "Postcode",
    "comment_status": "Property Status",
    "comment_status_reason": "Status reason",
    "triage_status": "Your assessment",
    "rental_income": "Rental Income",
    "life_insurance_total": "Life Insurance Total",
}
_GBP_SCALE = _Decimal("0.01")


def _validate_money(v) -> Money:
    if isinstance(v, Money):
        return v
    if isinstance(v, dict):
        amount = v.get("amount", 0)
        currency = v.get("currency", "GBP")
        if isinstance(amount, str):
            return Money(amount, currency)
        return Money(str(amount), currency)
    if isinstance(v, (int, float)):
        return Money(str(v), "GBP")
    if isinstance(v, _Decimal):
        return Money(str(v), "GBP")
    raise ValueError(f"Cannot convert {type(v)} to Money")


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _serialize_money(m) -> dict:
    # lucidlint: ignore record-shape wire-format dict — pydantic serializer payload, serialization boundary owns the
    return {
        "amount": str(m.amount.quantize(_GBP_SCALE)),
        "currency": m.currency,
    }


def _validate_quantity(v) -> Quantity:
    if isinstance(v, cast(type, Quantity)):
        return v
    if isinstance(v, dict):
        return Quantity(v.get("value", 0), v.get("unit", ""))
    raise ValueError(f"Cannot convert {type(v)} to Quantity")


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _serialize_quantity(q) -> dict:
    m = float(q.magnitude)
    # lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    return {"value": int(m) if m == int(m) else m, "unit": str(q.units)}




class MoneySchema:
    """Pydantic core-schema protocol implementation for ``money.Money``.

    ``money`` cannot declare ``__get_pydantic_core_schema__`` itself, so
    this class implements the pydantic v2 protocol and ``_install_third_party_schemas``
    attaches its classmethod to the ``Money`` class.
    """

    @classmethod
    def __get_pydantic_core_schema__(cls, _source, _handler) -> core_schema.CoreSchema:
        return core_schema.no_info_plain_validator_function(
            _validate_money,
            serialization=core_schema.plain_serializer_function_ser_schema(_serialize_money),
        )




class QuantitySchema:
    """Pydantic core-schema protocol implementation for ``pint.Quantity``.

    Mirrors ``MoneySchema``: ``pint`` cannot declare the protocol hook, so
    this class implements it and ``_install_third_party_schemas`` attaches
    its classmethod to the ``Quantity`` class.
    """

    @classmethod
    def __get_pydantic_core_schema__(cls, _source, _handler) -> core_schema.CoreSchema:
        return core_schema.no_info_plain_validator_function(
            _validate_quantity,
            serialization=core_schema.plain_serializer_function_ser_schema(_serialize_quantity),
        )


def _install_third_party_schemas() -> None:
    """Register pydantic schemas for third-party types (Money, Quantity).

    ``money``/``pint`` are declared dependencies imported at module top;
    the hooks are installed once. This IS the correct pydantic v2
    approach — ``__get_pydantic_core_schema__`` is an explicit protocol
    they support.
    """
    try:
        if not hasattr(Money, "__get_pydantic_core_schema__"):
            Money.__get_pydantic_core_schema__ = MoneySchema.__get_pydantic_core_schema__

        if not hasattr(Quantity, "__get_pydantic_core_schema__"):
            Quantity.__get_pydantic_core_schema__ = QuantitySchema.__get_pydantic_core_schema__
    except ImportError as e:
        logger.debug("money/pint unavailable; third-party pydantic schemas not installed: %s", e)
        return


_install_third_party_schemas()



T = TypeVar("T")


class UserInputNode(Node[T], Generic[T]):
    """A leaf node whose value is set externally by enrichment modules,
    WebSocket messages, or direct API calls.

    Call ``.push(value, source_label)`` to set a new value. This emits the
    ``changed`` signal so that downstream DerivedNodes re-compute.
    Persists to SQLite automatically on every push.
    """

    def __init__(self, node_id: str, value_type: type[T]) -> None:
        super().__init__(node_id, value_type)
        # Validate that property node IDs have a numeric RID prefix.
        # Non-numeric RIDs like "exp/" or "big_0/" are test data that
        # must not enter the production DB.
        if not _per.testing and "/" in node_id:
            rid = node_id.split("/")[0]
            # Allow known non-property prefixes (settings, global config nodes, etc.)
            if not rid.isdigit() and rid not in ("settings",):
                raise ValueError(
                    f"Blocked node creation: RID {rid!r} (from node_id {node_id!r}) "
                    f"contains non-digit characters. Property RIDs must be numeric.\n"
                    f"\n"
                    f"This means the node was requested with a test/scaffold RID. "
                    f"No data was written to the database.\n"
                    f"\n"
                    f"Do NOT attempt to work around this by changing the RID — the "
                    f"code path that created this node is using test data and should "
                    f"be run via pytest with standard isolation fixtures.\n"
                )
        self._value: T | None = None
        self._push_timestamp: datetime | None = None
        self._source_label: str = ""
        loaded = self._load_attempt_from_db()
        if loaded is not None and loaded.succeeded:
            self._value = loaded.value_or_none()
            self._source_label = self._load_persisted_label()

    def _load_persisted_label(self) -> str:
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
        self._push_timestamp = datetime.now(UTC)
        self._source_label = source_label

        # Reject source labels that indicate test data leaking into the
        # production DB.  Test fixtures set persistence.testing=True so
        # this guard is bypassed during test runs.
        if source_label in ("test", "tests") and not _per.testing:
            raise RuntimeError(
                f"Blocked push to {self._id!r}: source_label={source_label!r} is "
                f"reserved for test data. A code path attempted to write test data "
                f"to the production database without DB isolation. No data was written.\n"
                f"\n"
                f"This is a bug in the code path that triggered the push. If you are "
                f"seeing this during development, use pytest with the standard test "
                f"isolation fixtures (they set persistence.testing=True so this guard "
                f"is bypassed).\n"
            )

# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
        result_dict: dict[str, Any] = {
            "status": "succeeded",
            "value": self._adapter.dump_python(self._value),
            "source_label": source_label,
        }
        self._persist(result_dict)
        self.changed.emit()

    @override
    async def attempt(self) -> Attempt[T]:
        if self._value is not None:
            return Attempt.succeeded(self._value)
        return Attempt.pending()

    @override
    def latest_attempt(self) -> Attempt:
        # During a scenario evaluation (dag.evaluate), the staged
        # hypothetical attempt shadows the real value.
        staged = staged_attempt(self._id)
        if staged is not None:
            return staged
        if self._value is not None:
            return Attempt.succeeded(self._value)
        return Attempt.pending()

    @override
    async def build_provenance(self) -> Provenance:
        # Fall back to persistence timestamp for data that predates freshness tracking
        freshness = self._push_timestamp or self._persisted_at
        return Provenance(
            label=self.display_label,
            value=self._provenance_value(),
            source_type=SourceType.USER,
            freshness=freshness,
        )

    @property
    def display_label(self) -> str:
        """User-facing label for this source.

        Settings nodes get friendly per-setting names ("Mortgage Rate",
        "Sinking Fund Rate") derived from their node id; the persons
        node becomes "Household members". Falls back to the raw source
        label for user-entered sources like "Rightmove".
        """
        if self._id == "persons" or self._id.endswith("/persons"):
            return "Household members"
        if self._id.startswith("settings/"):
            stem = self._id.split("/", 1)[1]
            return _SETTING_LABELS.get(stem, stem.replace("_", " ").title())
        label = self._source_label or ""
        if label in ("db", "config", "migration", "settings", "sheet-migration", ""):
            # Per-property source nodes (e.g. "87650634/status") get a
            # friendly name from their stem; generic settings fall back
            # to the raw id stem.
            stem = self._id.split("/")[-1]
            return _NODE_STEM_LABELS.get(stem, stem.replace("_", " ").title())
        return label or self._id

    def _provenance_value(self):
        """JSON-safe projection of the stored value for provenance."""
        return project_value(self._value)

# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    @override
    async def to_json_value(self) -> dict[str, Any]:
        """Return a JSON-safe dict without provenance."""
        if self._value is None:
            return {"status": "pending", "value": None}
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
        return {
            "status": "succeeded",
            "value": self._adapter.dump_python(self._value),
        }
