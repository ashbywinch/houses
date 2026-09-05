"""Shared checks for committed commute payloads (drive / intersection / searches).

One home for the load-or-None, byte-identical-apart-from-generated_at, and
two-tier fail-fast idioms that every commute tool re-derives.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def fail(user_message: str, dev_detail: str) -> int:
    """Two-tier fail-fast exit (docs/coding-standards.md): a plain-language
    stderr line the user can act on, plus a logger.warning with the exact
    resolution — never one without the other."""
    print(user_message, file=sys.stderr)
    logger.warning(dev_detail)
    return 1


# lucidlint: ignore record-shape compares wire-format payload dicts — serialization boundary owns the shape
# lucidlint: ignore record-shape compares wire-format payload dicts — serialization boundary owns the shape
def same_payload(existing: dict[str, Any], new: dict[str, Any]) -> bool:
    """Byte-identical apart from ``generated_at`` (the determinism contract)."""
    if not isinstance(existing, dict) or not isinstance(new, dict):
        return False
    if json.dumps(existing.get("searches"), sort_keys=True) != json.dumps(new.get("searches"), sort_keys=True):
        return False
    if not isinstance(existing.get("metadata"), dict) or not isinstance(new.get("metadata"), dict):
        return False
    e_meta = {k: v for k, v in existing["metadata"].items() if k != "generated_at"}
    n_meta = {k: v for k, v in new["metadata"].items() if k != "generated_at"}
    return json.dumps(e_meta, sort_keys=True) == json.dumps(n_meta, sort_keys=True)

# lucidlint: ignore record-shape returns whatever the committed JSON held — serialization boundary owns the shape
def existing_payload(out_path: Path) -> dict[str, Any] | None:
    """Current payload, or None when absent/unreadable (will regenerate)."""
    if not out_path.exists():
        return None
    try:
        return json.loads(out_path.read_text())
    except json.JSONDecodeError:
        logger.warning("%s unreadable (corrupt?) — will regenerate", out_path)
        return None
