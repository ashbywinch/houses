"""The serialization library — how a boundary record becomes its wire form.

Used by every layer (`dag/`, `houses/`, `tools/`, `scripts/`): it depends on
nothing of ours, so it sits beside them rather than inside any one of them.

- `SerializableAsDict` is the base for a record whose dict IS its wire form.
- `MoneyValue` / `QuantityValue` annotate a field carrying `money.Money` /
  `pint.Quantity` — the serialiser lives on the field, never in a class beside
  the domain type.
- `omit_when_none` / `omit_when_empty` are the two per-field conditions for a
  key that is present only sometimes.

How to write a record, and when a whole-record serializer is warranted, is in
docs/coding-standards.md → *Boundary records*.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, TypedDict, TypeVar

from money import Money
from pint import Quantity
from pydantic import BaseModel, BeforeValidator, ConfigDict, PlainSerializer

type RecordWire = dict[str, Any]
"""A record's wire: the dict a client, a cache key or ``json.dumps`` takes, and
the shape every boundary record's ``to_dict`` returns."""

_GBP_SCALE = Decimal("0.01")


_T = TypeVar("_T")


def optional_parse(raw: Mapping[str, Any], key: str, parser: Callable[[Any], _T]) -> _T | None:
    """Parse the nested record under *key* only when present — the
    wire-parse idiom (an absent optional field is None, never a parse
    of a missing dict), one copy instead of one per record."""
    value = raw.get(key)
    return parser(value) if value else None


def _money_amount_str(m: Money) -> str:
    """Normalise a Money amount to a canonical 2-dp string."""
    return str(m.amount.quantize(_GBP_SCALE))


class MoneyWire(TypedDict):
    """The money wire shape: a canonical 2-dp amount plus currency.

    Named, so the field serialiser's output and its ``return_type`` are the
    same declaration — a key rename cannot drift silently.
    """

    amount: str
    currency: str


def _money_wire(m: Money) -> MoneyWire:
    """Money as its wire: the canonical 2-dp amount plus currency."""
    return MoneyWire(amount=_money_amount_str(m), currency=m.currency)


def _money_from_wire(value: Any) -> Any:
    """Accept the money wire shape as input, so a model can round-trip its
    own output (a persisted row re-read, a response re-validated)."""
    if isinstance(value, Mapping) and "amount" in value and "currency" in value:
        return Money(amount=str(value["amount"]), currency=str(value["currency"]))
    return value


type MoneyValue = Annotated[
    Money,
    BeforeValidator(_money_from_wire),
    PlainSerializer(_money_wire, return_type=MoneyWire),
]
"""A Money field that reads and writes ``{amount, currency}`` — the
serialiser lives on the type, so no parallel record is needed."""


class QuantityWire(TypedDict):
    """The quantity wire shape: a number plus its unit."""

    value: int | float
    unit: str


def _quantity_wire(q: Quantity) -> QuantityWire:
    """The quantity wire shape: a number plus its unit — exactly what
    ``DagJSONEncoder`` writes for a Quantity, so a payload and a stored row
    spell a duration the same way."""
    magnitude = float(q.magnitude)
    return QuantityWire(
        value=int(magnitude) if magnitude == int(magnitude) else magnitude, unit=str(q.units)
    )


def _quantity_from_wire(value: Any) -> Any:
    """Accept the quantity wire shape as input, so a model round-trips its own
    output (a persisted row re-read, a response re-validated)."""
    if isinstance(value, Mapping) and "value" in value and "unit" in value:
        return Quantity(value["value"], str(value["unit"]))
    return value


type QuantityValue = Annotated[
    Quantity,
    BeforeValidator(_quantity_from_wire),
    PlainSerializer(_quantity_wire, return_type=QuantityWire),
]
"""A Quantity field that reads and writes ``{value, unit}``."""


def omit_when_none(value: Any) -> bool:
    """A field condition: the key is present only when it is set.

    Pass it as ``Field(default=None, exclude_if=omit_when_none)`` — the rule
    sits on the field it governs, so a reader never has to hunt for it.
    """
    return value is None


def omit_when_empty(value: Any) -> bool:
    """A field condition: the key is dropped when it is falsy — an empty query
    parameter, which httpx would drop from the wire anyway."""
    return not value


class SerializableAsDict(BaseModel):
    """A record whose dict IS its wire form — the base for every boundary
    record, inbound (FastAPI dumps it) and outbound (we do).

    ``to_dict()`` is the one place the record becomes the wire a client, a
    cache key, or ``json.dumps`` takes. It is declared ``RecordWire``: named,
    because every boundary record's serialiser returns it, and mutable, because
    the web layer patches a payload at its projection boundary. Third-party
    field types (Money, Quantity) are arbitrary to pydantic and need
    ``arbitrary_types_allowed``; aliased fields stay settable by their python
    name, so a builder reads like the domain (``point_lat=…`` for a wire key of
    ``point.lat``).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    def to_dict(self) -> RecordWire:
        return self.model_dump(by_alias=True)


def asdict_serializable(obj: Any) -> Any:
    """Recursively convert a dataclass tree to JSON-serializable dicts.

    Like ``dataclasses.asdict()`` but also converts enums and Money to
    their values.
    """
    if isinstance(obj, Money):
        return _money_wire(obj)
    if isinstance(obj, Enum):
        return obj.value
    if dataclasses.is_dataclass(obj):
        return {f.name: asdict_serializable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {k: asdict_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [asdict_serializable(v) for v in obj]
    return obj
