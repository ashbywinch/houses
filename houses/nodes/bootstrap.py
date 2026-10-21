from __future__ import annotations

import logging

from dag.persistence import property_rids
from houses.nodes.property_nodes import PropertyNodes
from houses.property_registry import register_property

logger = logging.getLogger(__name__)


class _PropertyRidContract:
    """Rightmove property IDs are 6-10 digits; anything shorter or longer
    is test data that must never enter the production DB."""

    MIN_LENGTH = 6
    MAX_LENGTH = 10


def load_property_nodes_from_db() -> int:
    """Create PropertyNodes for every RID found in the DB.
    Called on normal startup. No sheet dependency.
    Nodes load their persisted values from the database automatically.
    """

    count = 0
    for rid in property_rids():
        # The RID contract is Rightmove's: all digits, 6-10 chars (see
        # UserInputNode's construction guard). Rows with any other RID are
        # TEST-DATA POLLUTION — they must never reach this DB, and if they
        # do, startup fails loudly instead of silently serving them:
        # delete the offending rows immediately (back the rows up first)
        # and fix whatever wrote them.
        if not rid.isdigit() or not _PropertyRidContract.MIN_LENGTH <= len(rid) <= _PropertyRidContract.MAX_LENGTH:
            raise RuntimeError(
                f"node_results contains test-data rows under invalid property "
                f"RID {rid!r}. Test data must never live in the real DB — "
                f"back up and DELETE those rows immediately, then fix the "
                f"code path that wrote them. See docs/dag-library.md → "
                f"'Thread rules', rule 6."
            )
        prop = PropertyNodes(rid)
        register_property(rid, prop)
        # PRD contract: reads and writes are non-blocking; recomputes are
        # scheduled by whatever makes them necessary.  A deploy re-stamps
        # only the classes whose referenced code changed (per-class
        # fingerprint, never 'every row') — schedule that background
        # recompute here, once, instead of on the read path.
        prop.schedule_code_stale_nodes()
        count += 1
    logger.info("Loaded %d properties from DB", count)
    return count
