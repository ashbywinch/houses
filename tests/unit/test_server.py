"""Tests for the FastAPI server endpoints — pure unit tests, no API calls."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from houses.server import app, extract_postcode

client = TestClient(app)


def _inject_session(c: TestClient) -> None:
    """Add a valid signed session cookie — /api/* routes require auth."""
    from houses.web.auth import _make_session_cookie

    c.cookies.set(
        "session",
        _make_session_cookie(
            email="simon@example.com",
            name="Simon",
            picture="",
            is_superuser=True,
        ),
    )


_inject_session(client)


class TestLifespanDbWiring:
    """The DAG persistence layer must honor HOUSES_SQLITE_PATH — the
    blue/green standby's smoke isolation depends on it (a standby that
    writes the live DB would corrupt prod during pre-switch testing)."""

    @pytest.mark.asyncio
    async def test_lifespan_wires_dag_db_to_settings_path(self):
        """Regression: startup called init_dag_db() bare, so the DAG
        persistence fell back to its hardcoded data/houses.db and ignored
        settings.sqlite_path — a standby's writes landed in the LIVE DB.
        After startup, DAG writes must go to settings.sqlite_path.  The
        lifespan runs for real (its background tasks are canceled on exit);
        the two module globals it sets are restored in finally so nothing
        leaks to later tests."""
        from pathlib import Path

        import dag.scheduler as scheduler_mod
        import houses.server as server_mod
        from dag import persistence as p
        from houses.nodes import settings as nodes_settings
        from houses.settings import settings

        # The lifespan refuses to start without OAuth configured, and CI has
        # no .env — set the fields for the duration (the pydantic settings
        # singleton has no injection seam; direct assignment + restore).
        prev = (
            settings.web_client_id,
            settings.web_client_secret,
            settings.session_secret,
        )
        settings.web_client_id = settings.web_client_id or "test-client"
        settings.web_client_secret = settings.web_client_secret or "test-secret"
        settings.session_secret = settings.session_secret or "test-session"
        prev_app_mode = nodes_settings._app_mode
        prev_after_refresh = scheduler_mod.get_scheduler()._after_refresh_callback
        p.DB_PATH = None  # module state, deliberately: assert what startup wires
        try:
            async with server_mod.lifespan(server_mod.app):
                assert Path(settings.sqlite_path) == p.DB_PATH, (
                    f"DAG persistence DB_PATH={p.DB_PATH!r} — startup must wire "
                    "init_dag_db(settings.sqlite_path)"
                )
        finally:
            nodes_settings._app_mode = prev_app_mode
            scheduler_mod.get_scheduler()._after_refresh_callback = prev_after_refresh
            settings.web_client_id, settings.web_client_secret, settings.session_secret = prev


class TestExtractPostcode:
    def test_full_postcode(self):
        assert extract_postcode("High Street, Some Town, RG14 1AA") == "RG14 1AA"

    def test_outcode_only(self):
        assert extract_postcode("Shoppenhangers Road, Maidenhead, SL6") == "SL6"

    def test_no_postcode(self):
        assert extract_postcode("Some Road, Town") == ""

    def test_empty_string(self):
        assert extract_postcode("") == ""

    def test_postcode_at_start(self):
        assert extract_postcode("SW1A 1AA London") == "SW1A 1AA"

    def test_london_outcode(self):
        assert extract_postcode("Victoria Street, London, SW1E") == "SW1E"


class TestHealth:
    def test_health_returns_ok(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestInjectProperty:
    VALID_PAYLOAD = {
        "url": "https://www.rightmove.co.uk/properties/123456789",
        "address": "High Street, Some Town, RG14 1AA",
    }

    def test_rejects_invalid_types(self):
        payload = {**self.VALID_PAYLOAD, "bedrooms": "three"}
        resp = client.post("/api/properties", json=payload)
        assert resp.status_code == 422

    def test_scraped_postcode_is_seeded_into_the_dag(self):
        """Regression: POST /api/properties scrapes the listing and must
        seed the scraped postcode into the DAG's postcode node.  The
        sources dict used to omit 'postcode', so the node stayed pending
        forever and every has-car commute (park_and_ride depends on
        postcode) was permanently stuck 'pending' instead of computing."""
        from houses.property_registry import get_property as get_registry_property
        from tests.helpers import inject_server_deps

        fake_scrape = MagicMock()
        fake_scrape.address = "Penwood Lane, Marlow, Buckinghamshire, SL7 2AP"
        fake_scrape.postcode = "SL7 2AP"
        fake_scrape.bedrooms = 4
        fake_scrape.price = 800000
        fake_scrape.latitude = 51.5676
        fake_scrape.longitude = -0.7842
        fake_scrape.url = "https://www.rightmove.co.uk/properties/89498715"

        scrape_fn = AsyncMock(return_value=fake_scrape)
        with inject_server_deps(scrape_fn=scrape_fn):
            resp = client.post(
                "/api/properties",
                json={"url": "https://www.rightmove.co.uk/properties/89498715"},
            )
        assert resp.status_code == 200, resp.text
        scrape_fn.assert_called_once()

        # The postcode node must have the scraped value — NOT stay pending
        prop = get_registry_property("89498715")
        assert prop is not None
        postcode_attempt = prop.postcode.latest_attempt()
        assert postcode_attempt.succeeded, (
            f"postcode node must be seeded, got {postcode_attempt.status}: {postcode_attempt.error}"
        )
        assert postcode_attempt.value_or_none() == "SL7 2AP"


class TestBackfillView:
    def test_no_payload_returns_legacy_200(self):
        """POST /api/properties with no JSON body used to be the sheet
        batch-refresh entry point; it is now a no-op but must still answer
        200 (legacy endpoint contract)."""
        resp = client.post("/api/properties")
        assert resp.status_code == 200
