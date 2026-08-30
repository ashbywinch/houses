"""Backfill postcodes for API-added properties whose address lacks one.

The postcode node is derived from the best address (PostcodeNode), so a
known postcode is backfilled by folding it INTO the address — the
address stays the single fact.  Idempotent: skips when the address
already carries the postcode.

Loads the persisted properties first — a fresh in-memory PropertyNodes
has no address to upgrade.  Persists to the DB; then call
POST /api/admin/regenerate on the running app to cascade through the
in-memory nodes it already holds.
"""
import asyncio

from dag.scheduler import flush_processor
from houses.location import upgrade_address
from houses.nodes.bootstrap import load_property_nodes_from_db
from houses.services_provider import get_services

BACKFILL = {
    "173677193": "RG4 9EJ",
    "89498715": "SL7 2AP",
}


async def main() -> None:
    load_property_nodes_from_db()
    await flush_processor()
    registry = get_services().property_registry

    for rid, postcode in BACKFILL.items():
        prop = registry.get(rid)
        if prop is None:
            print(f"{rid}: not found in the DB — skip")
            continue
        ba = await prop.best_address.attempt()
        addr = ba.value_or_none() if ba.succeeded else ""
        if not addr:
            print(f"{rid}: no address in the DB — nothing to upgrade")
            continue
        upgraded = upgrade_address(addr, postcode)
        if upgraded == addr:
            print(f"{rid}: address already carries {postcode} — skip")
            continue
        prop.corrected_address.push(upgraded, "backfill")
        print(f"{rid}: folded {postcode} into the address")
    await flush_processor()


if __name__ == "__main__":
    asyncio.run(main())
