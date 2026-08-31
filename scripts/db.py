"""Shared helpers for one-shot migration/extraction scripts."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def conn() -> sqlite3.Connection:
    """Open the repo DB read-write; exit with a plain-language error if absent."""
    db = Path("data/houses.db")
    if not db.exists():
        raise SystemExit("data/houses.db not found")
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn
