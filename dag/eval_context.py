"""Task-local staging for pure scenario evaluation (Part D).

``dag.evaluate.evaluate()`` computes what nodes WOULD be under overridden
inputs without mutating real node state. The staged attempts live in the
ContextVar below, consulted by ``latest_attempt()`` (see
``dag.derived_node`` / ``dag.user_input_node``) so compute bodies that
read deps through the sync path — expressions, ``Ref``, predicates,
``_get_active_deps`` — see the hypothetical values transparently, exactly
as they see real values during refresh.

The context is task-local and reset after every evaluation, so the real
refresh path (a different task, no staging) is never affected.
"""

from __future__ import annotations

import contextvars
from typing import Any

from dag.attempt import Attempt

# Staged node_id -> Attempt during an evaluation; None outside one.
eval_staging_ctx: contextvars.ContextVar[dict[str, Attempt] | None] = contextvars.ContextVar(
    "dag_eval_ctx", default=None
)


def staged_attempt(node_id: str) -> Attempt | None:
    """Return the staged attempt for a node during evaluation, else None."""
    ctx = eval_staging_ctx.get()
    return ctx.get(node_id) if ctx is not None else None


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def build_staging(overrides: dict[str, Any]) -> dict[str, Attempt]:
    """Seed a staging map from ``node_id -> candidate value`` overrides.

    Overridden inputs become succeeded Attempts marked hypothetical so
    provenance can distinguish them from real values.
    """
    staging: dict[str, Attempt] = {}
    for node_id, value in overrides.items():
        staging[node_id] = Attempt.succeeded(value, metadata={"hypothetical": True})
    return staging
