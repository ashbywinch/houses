from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Provenance:
    """Tracks where a value came from and which deps contributed to it.

    ``label`` is a human-readable string shown in the UI ("Rightmove",
    "TfL API", "User correction").

    ``source_attempts`` maps dep node IDs to the Attempt values that
    contributed to this result. The UI walks this tree to render
    hierarchical provenance.
    """
    label: str = ""
    source_attempts: dict[str, Attempt] = field(default_factory=dict)


class Attempt[T]:
    """A value that may have succeeded or failed, with provenance.

    ``Attempt.succeeded(value, provenance)`` — computation produced a value.
    ``Attempt.impossible(error)`` — computation failed.
    """

    def __init__(self, value: T | None = None, error: str = "",
                 provenance: Provenance | None = None) -> None:
        self._value = value
        self._error = error
        self._provenance = provenance or Provenance()

    @property
    def is_succeeded(self) -> bool:
        return not self._error

    @property
    def provenance(self) -> Provenance:
        return self._provenance

    def value_or_none(self) -> T | None:
        return self._value

    @classmethod
    def succeeded(cls, value: T, provenance: Provenance) -> Attempt[T]:
        return cls(value=value, error="", provenance=provenance)

    @classmethod
    def impossible(cls, error: str) -> Attempt[T]:
        return cls(value=None, error=error, provenance=Provenance())
