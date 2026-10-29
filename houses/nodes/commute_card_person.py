"""The person as the list card's commute row states them."""

from __future__ import annotations

from serialization import SerializableAsDict


class CommuteCardPerson(SerializableAsDict):
    """Only the display name — the card never shows the rest of the Person."""

    name: str = ""
