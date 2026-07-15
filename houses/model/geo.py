from __future__ import annotations

import re

_POSTCODE_PATTERN = re.compile(r"[A-Z]{1,2}\d{1,2}[A-Z]?\s\d[A-Z]{2}$")


def is_single_property_address(address: str | None) -> bool:
    if not address:
        return False
    addr = address.strip()
    first_word = addr.split()[0] if addr else ""
    if not first_word or not first_word[0].isdigit():
        return False
    return bool(_POSTCODE_PATTERN.search(addr))
