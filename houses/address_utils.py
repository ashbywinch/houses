"""Shared address normalisation helpers.

Both the VOA council-tax lookup and the EPC lookup match addresses
against external records (postcode pages / certificate rows) and used
to duplicate ``_normalise`` plus a postcode-strip helper.  This is the
canonical home for those.
"""

from __future__ import annotations

import re

POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s+(\d[A-Z]{2})\b")


def normalise(text: str) -> str:
    """Strip whitespace, uppercase, remove punctuation for comparison."""
    return re.sub(r"[^A-Z0-9 ]", "", text.upper().strip())


def strip_postcode(tokens: list[str], address: str) -> list[str]:
    """Drop the postcode tokens from a normalized token list.

    VOA rows end with the postcode while the query may carry a county
    between locality and postcode ("2 WILLOWMEAD GARDENS, MARLOW,
    BUCKINGHAMSHIRE, SL7 1HW").  The county token must not break the
    token-aligned prefix/suffix comparisons in the matching rules.
    """
    m = POSTCODE_RE.search(normalise(address))
    if not m:
        return tokens
    drop = set(m.groups())
    return [t for t in tokens if t not in drop]
