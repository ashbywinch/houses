"""Create PropertyNodes from existing DB — pick a RID with node_results data."""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from houses.settings import settings
from houses.nodes.property_nodes import PropertyNodes
from houses.property_registry import register_property
from houses.services import SETTINGS_SOURCE_CACHE
# lucidlint: ignore private-import intra-package helper import
from houses.services_provider import _request_services
from tests.helpers import make_services

SETTINGS_SOURCE_CACHE.clear()
token = _request_services.set(make_services())

conn = sqlite3.connect(settings.sqlite_path)
has_data = conn.execute(
    "SELECT DISTINCT substr(node_id, 1, instr(node_id, '/') - 1) as rid "
    "FROM node_results WHERE node_id LIKE '%/best_address'"
).fetchall()
conn.close()

if not has_data:
    print("No RIDs with node_results for best_address")
    sys.exit(1)

sample_rid = has_data[0][0]
print(f"RID: {sample_rid}")

prop = PropertyNodes(sample_rid)
register_property(sample_rid, prop)


async def check():
    for key, sel in prop.commute_selectors.items():
        a = await sel.attempt()
        print(f"  {key:40s} status={a.status:12s}")
        if a.succeeded:
            val = a.value_or_none()
            if isinstance(val, dict):
                print(f"    duration={val.get('duration')}")

    for name in ["rightmove_address", "best_address", "best_location", "postcode"]:
        node = getattr(prop, name, None)
        if node:
            a = await node.attempt()
            print(f"  {name:25s} status={a.status:12s}")


asyncio.run(check())

_request_services.reset(token)
