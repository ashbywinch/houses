"""The EPC register search's result rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EpcSearchResult:
    """The EPC register's certificate rows, as returned by the search."""

    certificates: list[Any]