"""JSON serialization utilities extracted from the old enrichment pipeline.

Functions moved here from ``enrichment_runner.py`` before its deletion.
"""

from __future__ import annotations

import dataclasses
from enum import Enum
from typing import Any

from money import Money


def asdict_serializable(obj: Any) -> Any:
    """Recursively convert a dataclass tree to JSON-serializable dicts.

    Like ``dataclasses.asdict()`` but also converts enums and Money to
    their values.
    """
    if isinstance(obj, Money):
        return float(obj.amount)
    if isinstance(obj, Enum):
        return obj.value
    if dataclasses.is_dataclass(obj):
        return {f.name: asdict_serializable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {k: asdict_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [asdict_serializable(v) for v in obj]
    return obj
