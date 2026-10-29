"""The destination as the list card's commute row states it."""

from __future__ import annotations

from serialization import SerializableAsDict


class CommuteCardDestination(SerializableAsDict):
    """Only the address the row's Maps link navigates to — the card never
    shows the destination's frequency or accepted modes."""

    address: str = ""
