"""Regression: a what-if apply must push fresh summaries to websocket clients.

User report (2026-09-08): applying a scenario updated the backend
figures but every card kept its old monthly total until a manual
reload. Root cause: the summary-broadcast seam was unwired — nothing
called ``notify_node_refreshed``, so ``property_updated`` messages
never fired and the phone's store stayed on stale summaries.

The contract: with the production after-refresh hook registered (as
server startup does), a scenario apply must deliver a
``property_updated`` message for the property whose figures changed,
and the pushed summary must carry the UPDATED monthly totals.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from money import Money

from houses.geopoint import GeoPoint
from houses.model.domain import HomeCoOwner, Person, PlaceOfInterest
from houses.nodes.property_nodes import PropertyNodes
from houses.property_registry import register_property
from houses.server import app
from houses.services_provider import get_services as _get_services
from houses.web.auth import get_serializer

REAL_BRACKNELL_ADDRESS = "Broad Lane, Bracknell"
_TEST_LAT = 51.4934
_TEST_LON = -0.0098


def _poi(label: str, trips: int, address: str = "Pimlico Rd, London") -> PlaceOfInterest:
    return PlaceOfInterest(
        label=label,
        address=address,
        trips_per_week=trips,
        weeks_per_year=46,
        acceptable_modes=("car",),
    )


def _lorena() -> Person:
    return Person(
        name="Lorena",
        has_car=False,
        email="lorena@example.com",
        places_of_interest=(
            PlaceOfInterest(
                label="Aldgate",
                address="Aldgate station, London",
                trips_per_week=2,
                weeks_per_year=46,
                acceptable_modes=("walk",),
            ),
        ),
    )


class _FakeWsClient:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, msg: str) -> None:
        self.sent.append(msg)


async def _prime_property(rid: str) -> PropertyNodes:
    """The signed-in app, a primed property, and the production seam
    (after-refresh hook → broadcaster) wired to the running loop."""
    import houses.server as server_mod
    from dag.scheduler import set_after_refresh

    svc = _get_services()
    svc.persons_source.push(
        [
            Person(
                name="Simon",
                has_car=True,
                email="simon@example.com",
                is_superuser=True,
                home_sale_price=Money(amount="550000", currency="GBP"),
                outstanding_mortgage=Money(amount="373000", currency="GBP"),
                home_co_owners=(HomeCoOwner(name="Lorena", share=50),),
                places_of_interest=(
                    _poi("Pimlico", 1),
                    _poi("Bracknell", 1, REAL_BRACKNELL_ADDRESS),
                ),
            ),
            _lorena(),
        ],
        "user",
    )
    prop = PropertyNodes(rid)
    prop.rightmove_price.push(Money(amount="500000", currency="GBP"), "test")
    prop.rightmove_address.push("1 Test St", "test")
    prop.rightmove_bedrooms.push("3", "test")
    prop.rightmove_location.push(GeoPoint(_TEST_LAT, _TEST_LON), "test")
    prop.corrected_address.push("1 Test St, SW1V 2QQ", "test")
    prop.precise_location.push(GeoPoint(_TEST_LAT, _TEST_LON), "test")
    prop.user_entered_address.push("1 Test St, SW1V 2QQ", "test")
    prop.works_estimates.push({}, "test")
    prop.rental_income.push(Money(amount="0", currency="GBP"), "test")
    prop.comment_status.push("", "test")
    register_property(rid, prop)

    loop = asyncio.get_running_loop()
    server_mod._main_loop = loop
    set_after_refresh(server_mod._on_node_refreshed)
    return prop


async def _wire_broadcasts() -> tuple[Any, asyncio.Task]:
    import houses.web.broadcaster as bcast

    fake = _FakeWsClient()
    bcast._websocket_clients.add(cast(Any, fake))
    task = asyncio.create_task(bcast._broadcaster())
    return fake, task


async def _stop_broadcasts(fake: Any, task: asyncio.Task) -> None:
    import houses.web.broadcaster as bcast

    bcast._websocket_clients.discard(cast(Any, fake))
    task.cancel()


def _messages(fake: Any, kind: str | None = None) -> list[dict]:
    out = []
    for msg in fake.sent:
        parsed = json.loads(msg)
        if kind is None or parsed.get("type") == kind:
            out.append(parsed)
    return out


@pytest.mark.asyncio
async def test_property_node_refresh_pushes_a_summary_not_a_node_update():
    """The phone renders cards from property summaries. A property node
    finishing its recompute must push ONE property_updated summary and
    must NOT push the internal node's raw node_updated payload."""
    import houses.server as server_mod
    from dag.scheduler import flush_processor

    rid = "42555556"
    prop = await _prime_property(rid)
    await flush_processor()
    fake, task = await _wire_broadcasts()
    try:
        fake.sent.clear()
        server_mod._on_node_refreshed(prop.commute_breakdown)
        await asyncio.sleep(0.1)
        for _ in range(20):
            await asyncio.sleep(0.1)
            if _messages(fake, "property_updated"):
                break

        summaries = [m for m in _messages(fake, "property_updated") if m.get("rid") == rid]
        assert summaries, "the property summary must be broadcast"
        node_updates = [m for m in _messages(fake, "node_updated") if m.get("rid") == rid]
        assert node_updates == [], (
            "internal DAG nodes must not be broadcast as node_updated — nothing on the phone renders a raw DAG node"
        )
    finally:
        await _stop_broadcasts(fake, task)


@pytest.mark.asyncio
async def test_settings_node_refresh_pushes_the_settings_payload():
    """A settings node refresh (persons, thresholds) must push ONE
    settings_updated payload — the settings the phone re-renders from —
    and no per-node node_updated."""
    import houses.server as server_mod
    from dag.scheduler import flush_processor
    from houses.services_provider import get_services as _get_services

    await _prime_property("42555556")
    await flush_processor()
    fake, task = await _wire_broadcasts()
    try:
        fake.sent.clear()
        svc = _get_services()
        server_mod._on_node_refreshed(svc.persons_source)
        await asyncio.sleep(0.1)
        for _ in range(20):
            await asyncio.sleep(0.1)
            if _messages(fake, "settings_updated"):
                break

        pushes = _messages(fake, "settings_updated")
        assert len(pushes) == 1, f"settings pushes must coalesce to one, got {len(pushes)}"
        payload = pushes[0]["data"]
        assert payload["persons"]["succeeded"] is True
        assert payload["what_if_active"] is False
        assert _messages(fake, "node_updated") == []
    finally:
        await _stop_broadcasts(fake, task)


@pytest.mark.asyncio
async def test_scenario_apply_pushes_updated_summary_to_websocket_clients():
    import houses.server as server_mod
    import houses.web.broadcaster as bcast
    from dag.scheduler import flush_processor, set_after_refresh
    from houses.services_provider import get_services as _get_services

    svc = _get_services()
    svc.persons_source.push(
        [
            Person(
                name="Simon",
                has_car=True,
                email="simon@example.com",
                is_superuser=True,
                home_sale_price=Money(amount="550000", currency="GBP"),
                outstanding_mortgage=Money(amount="373000", currency="GBP"),
                home_co_owners=(HomeCoOwner(name="Lorena", share=50),),
                places_of_interest=(
                    _poi("Pimlico", 1),
                    _poi("Bracknell", 1, REAL_BRACKNELL_ADDRESS),
                ),
            ),
            _lorena(),
        ],
        "user",
    )
    rid = "42555556"
    prop = PropertyNodes(rid)
    prop.rightmove_price.push(Money(amount="500000", currency="GBP"), "test")
    prop.rightmove_address.push("1 Test St", "test")
    prop.rightmove_bedrooms.push("3", "test")
    prop.rightmove_location.push(GeoPoint(_TEST_LAT, _TEST_LON), "test")
    prop.corrected_address.push("1 Test St, SW1V 2QQ", "test")
    prop.precise_location.push(GeoPoint(_TEST_LAT, _TEST_LON), "test")
    prop.user_entered_address.push("1 Test St, SW1V 2QQ", "test")
    prop.works_estimates.push({}, "test")
    prop.rental_income.push(Money(amount="0", currency="GBP"), "test")
    prop.comment_status.push("", "test")
    register_property(rid, prop)
    await flush_processor()

    client = TestClient(app)
    claims = {
        "email": "simon@example.com",
        "name": "Simon",
        "picture": "",
        "is_superuser": True,
        "impersonating": None,
    }
    client.cookies.set("session", get_serializer().dumps(claims))

    detail = client.get(f"/api/properties/{rid}/detail").json()
    baseline = detail["affordability"]["group_monthly_cost"]["value"]["couple"]["value"]
    assert Decimal(baseline) > 0

    # Wire the production seam exactly as server startup does: the
    # DAG after-refresh hook hands updates to the broadcaster.
    loop = asyncio.get_running_loop()
    server_mod._main_loop = loop
    set_after_refresh(server_mod._on_node_refreshed)

    fake = _FakeWsClient()
    bcast._websocket_clients.add(cast(Any, fake))
    broadcaster_task = asyncio.create_task(bcast._broadcaster())
    try:
        resp = client.post(
            "/api/what-if/apply",
            json={
                "persons": [
                    {
                        "name": "Simon",
                        "places_of_interest": [
                            {
                                "label": "Pimlico",
                                "address": "Pimlico Rd, London",
                                "trips_per_week": 0,
                                "weeks_per_year": 46,
                                "acceptable_modes": ["car"],
                            },
                        ],
                    },
                ]
            },
        )
        assert resp.status_code == 200, resp.text
        await flush_processor()

        # The debounce (0.4s) plus the loop hops must land the summary
        # push; poll rather than sleep the maximum.
        pushed: dict | None = None
        for _ in range(40):
            await asyncio.sleep(0.1)
            for msg in fake.sent:
                parsed = json.loads(msg)
                if parsed.get("type") == "property_updated" and parsed.get("rid") == rid:
                    pushed = parsed
            if pushed is not None:
                break

        assert pushed is not None, (
            "a scenario apply must push a property_updated summary to "
            "connected clients — without it the phone keeps stale totals"
        )
        couple = pushed["data"]["group_monthly_cost"]["value"]["couple"]["value"]
        assert Decimal(couple) < Decimal(baseline), (
            f"the pushed summary must carry the UPDATED totals: baseline={baseline} pushed={couple}"
        )
    finally:
        bcast._websocket_clients.discard(cast(Any, fake))
        broadcaster_task.cancel()
        bcast._pending_notify_rids.clear()
        server_mod._main_loop = None
