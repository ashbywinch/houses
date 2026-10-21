"""The schools section of both property documents (primary and secondary)."""

from __future__ import annotations

from typing import TypedDict

from houses.nodes.school_entry import SchoolEntry


class SchoolsBlock(TypedDict, total=False):
    """Wire shape of the schools block (primary and secondary)."""

    primary: SchoolEntry
    secondary: SchoolEntry
