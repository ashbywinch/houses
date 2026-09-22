"""Regression: the startup sweep must judge a consumer stale even when
the dependency has not been hydrated yet.

The 2026-09-19 census: 42555556/geocode persisted 09-13 with the dep
stamp best_address -> 09-13T18:16:54; best_address advanced to
09-19T11:52 via a scrape re-seed. The geocode row was never rewritten
despite days of restarts. Root cause candidate: the sweep's _is_stale
compares the stamp against dep._db_created_at — which is "" until the
dep is hydrated, and the check literally `continue`s past the stamp
comparison when that happens. The sweep does not pre-hydrate deps, so
the consumer is judged fresh and never scheduled.
"""

from datetime import UTC, datetime
from typing import override

import pytest

from dag.attempt import Attempt
from dag.derived_node import DerivedNode
from dag.persistence import save_node_result
from dag.regenerate import schedule_code_stale_nodes
from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode


class _Upper(DerivedNode[str]):
    """A trivial derived node: upper-cases its input."""

    def __init__(self, node_id, *, src):
        super().__init__(node_id, str, (src,))

    @override
    async def compute(self, src: Attempt) -> Attempt:
        val = src.value_or_none()
        if val is None:
            return Attempt.pending()
        return Attempt.succeeded(str(val).upper())


@pytest.mark.asyncio
async def test_sweep_reschedules_a_stamp_stale_consumer_without_predraining_the_dep():
    # First life: both rows persisted together.
    a = UserInputNode("hs_a", str)
    b = _Upper("hs_b", src=a)
    a.push("one", "test")
    await flush_processor()
    assert (await b.attempt()).value_or_none() == "ONE"

    # The dependency's row advances WITHOUT the consumer recomputing —
    # the migration/scrape end-run that writes rows directly.
    save_node_result(
        "hs_a",
        {"status": "succeeded", "value": "two"},
        created_at=datetime.now(UTC).isoformat(),
    )

    # Restart: fresh instances. The CONSUMER hydrates (its attempt is
    # served); the DEP is deliberately left untouched — exactly how the
    # startup sweep finds the graph. The sweep must still see the stamp
    # mismatch and reschedule the consumer.
    a2 = UserInputNode("hs_a", str)
    b2 = _Upper("hs_b", src=a2)
    assert (await b2.attempt()).value_or_none() == "ONE"

    stale = schedule_code_stale_nodes([a2, b2])
    assert b2 in stale, (
        "the sweep must reschedule a consumer whose recorded dep-stamp "
        "predates its dep's current row — even when the dep has not been "
        "hydrated yet (its row is newer than the recorded stamp)"
    )
    await flush_processor()
    assert (await b2.attempt()).value_or_none() == "TWO"
    assert not b2._is_stale()
