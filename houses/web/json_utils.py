"""JSON serialization utilities extracted from the old enrichment pipeline.

Functions moved here from ``enrichment_runner.py`` before its deletion.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from enum import Enum
from typing import Any

from money import Money

_GBP_SCALE = Decimal("0.01")


@dataclasses.dataclass(frozen=True)
class MoneyJson:
    """The serialized money wire shape: a canonical 2-dp amount plus currency."""

    amount: str
    currency: str

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction mirrors the money wire shape (coding-standards.md)
        return dict(amount=self.amount, currency=self.currency)


def _money_amount_str(m: Money) -> str:
    """Normalise a Money amount to a canonical 2-dp string."""
    return str(m.amount.quantize(_GBP_SCALE))


def asdict_serializable(obj: Any) -> Any:
    """Recursively convert a dataclass tree to JSON-serializable dicts.

    Like ``dataclasses.asdict()`` but also converts enums and Money to
    their values.
    """
    if isinstance(obj, Money):
        return MoneyJson(amount=_money_amount_str(obj), currency=obj.currency).to_dict()
    if isinstance(obj, Enum):
        return obj.value
    if dataclasses.is_dataclass(obj):
        return {f.name: asdict_serializable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {k: asdict_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [asdict_serializable(v) for v in obj]
    return obj
