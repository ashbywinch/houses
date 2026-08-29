"""Backfill postcodes for API-added properties whose address lacks one.

The postcode node is derived from the best address (PostcodeNode), so a
known postcode is backfilled by folding it INTO the address — the
address stays the single fact.  Idempotent: skips when the address
already carries the postcode.

Persists to the DB; then call POST /api/admin/regenerate on the running
app to cascade through the in-memory nodes it already holds.
"""
import asyncio

from houses.location import upgrade_address
from houses.nodes.property_nodes import PropertyNodes

BACKFILL = {
    "173677193": "RG4 9EJ",
    "89498715": "SL7 2AP",
}


async def main() -> None:

    for rid, postcode in BACKFILL.items():
        prop = PropertyNodes(rid)
        ba = await prop.best_address.attempt()
        addr = ba.value_or_none() if ba.succeeded else ""
        if not addr:
            print(f"{rid}: no address yet — nothing to upgrade")
            continue
        upgraded = upgrade_address(addr, postcode)
        if upgraded == addr:
            print(f"{rid}: address already carries {postcode} — skip")
            continue
        prop.corrected_address.push(upgraded, "backfill")
        print(f"{rid}: folded {postcode} into the address")


if __name__ == "__main__":
    asyncio.run(main())
