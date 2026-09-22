"""Regression: a stamp-stale consumer (dep row advanced past its recorded
stamp) must converge to NOT-stale once rescheduled and recomputed.

The 2026-09-19 census shape: 42555556/geocode persisted 09-13 recording
best_address's 09-13 stamp; best_address advanced to 09-19 via a scrape
re-seed; the geocode row was never rewritten across days of restarts.
The sweep-side question (does the reset schedule it?) is answered by
schedule_code_stale_nodes; this test pins the CONVERGENCE side: after
the sweep reschedules and the drain recomputes, the consumer must no
longer report stale — an out-of-date row must not re-enqueue itself on
every sweep, and a page must not see it as freshly computed.
"""

from datetime import UTC, datetime

import pytest

from dag.persistence import save_node_result
from dag.regenerate import schedule_code_stale_nodes
from dag.scheduler import flush_processor
from dag.user_input_node import UserInputNode
from houses.nodes.geocode_node import GeocodeNode


@pytest.mark.asyncio
async def test_a_stamp_stale_geocode_node_converges_after_the_sweep_reschedules_it():
    addr = UserInputNode("rg_addr", str)
    geo = GeocodeNode("rg_geocode", best_address=addr)
    addr.push("1 Isambard Road, Southall", "test")
    await flush_processor()
    assert (await geo.attempt()).succeeded

    # best_address advances WITHOUT the geocode recomputing — the
    # migration/scrape end-run that writes rows directly.
    save_node_result(
        "rg_addr",
        {"status": "succeeded", "value": "2 Isambard Road, Southall"},
        created_at=datetime.now(UTC).isoformat(),
    )

    # Restart: fresh instances, exactly as load_property_nodes_from_db
    # builds them.
    addr2 = UserInputNode("rg_addr", str)
    geo2 = GeocodeNode("rg_geocode", best_address=addr2)

    stale = schedule_code_stale_nodes([addr2, geo2])
    assert geo2 in stale, "the sweep must reschedule the stamp-stale geocode node"
    await flush_processor()
    assert (await geo2.attempt()).succeeded
    assert not geo2._is_stale(), (
        "after the sweep rescheduled it and the drain recomputed it, the "
        "geocode node must converge to fresh — a recomputed row that still "
        "reports stale would re-enqueue itself on every sweep and keep a "
        "newly computed value forever 'pending refresh'"
    )
