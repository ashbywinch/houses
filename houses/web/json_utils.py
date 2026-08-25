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


def _money_amount_str(m: Money) -> str:
    """Normalise a Money amount to a canonical 2-dp string."""
    return str(m.amount.quantize(_GBP_SCALE))


def asdict_serializable(obj: Any) -> Any:
    """Recursively convert a dataclass tree to JSON-serializable dicts.

    Like ``dataclasses.asdict()`` but also converts enums and Money to
    their values.
    """
    if isinstance(obj, Money):
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
        return {"amount": _money_amount_str(obj), "currency": obj.currency}
    if isinstance(obj, Enum):
        return obj.value
    if dataclasses.is_dataclass(obj):
        return {f.name: asdict_serializable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {k: asdict_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [asdict_serializable(v) for v in obj]
    return obj
