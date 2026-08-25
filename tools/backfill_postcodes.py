"""Backfill postcodes for API-added properties whose postcode node was
never seeded (the endpoint used to omit 'postcode' from its sources).
Idempotent — only pushes when the node is still pending.

Persists to the DB; then call POST /api/admin/regenerate on the running
app to cascade through the in-memory nodes it already holds.
"""
import asyncio

from dag.user_input_node import UserInputNode
from houses.nodes.property_nodes import PropertyNodes

BACKFILL = {
    "173677193": "RG4 9EJ",
    "89498715": "SL7 2AP",
}


async def main() -> None:

    for rid, postcode in BACKFILL.items():
        prop = PropertyNodes(rid)
        pc: UserInputNode = prop.postcode
        att = pc.latest_attempt()
        if att.succeeded and att.value_or_none():
            print(f"{rid}: postcode already set ({att.value_or_none()}) — skip")
            continue
        pc.push(postcode, "backfill")
        print(f"{rid}: pushed postcode {postcode}")


if __name__ == "__main__":
    asyncio.run(main())
