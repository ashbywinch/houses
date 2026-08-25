"""FastAPI app — /inject-property endpoint, startup/shutdown."""

import asyncio
import logging
import os
import subprocess
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from money import Money

import houses.property_registry as _property_registry
import houses.services as _services_mod
import houses.services_provider as _sp
import houses.town_desc as _town_desc
import houses.web.broadcaster as _broadcaster_mod
from dag.persistence import init_db as init_dag_db
from dag.persistence import property_rids
from dag.scheduler import set_after_refresh
from dag.scheduler import start_processor as _start_processor
from houses.admin_router import admin_router
from houses.context import get_client_factory, get_scrape_fn
from houses.database import close_db as close_app_db
from houses.database import init_db as init_app_db
from houses.location import extract_postcode
from houses.nodes.bootstrap import load_property_nodes_from_db, load_property_nodes_from_rows
from houses.nodes.cutover import push_enriched_property
from houses.nodes.property_nodes import PropertyNodes
from houses.nodes.settings import set_app_mode
from houses.property import EnrichedProperty, Property
from houses.rightmove_scraper import RightmoveProperty, stop_chrome
from houses.services import Services
from houses.settings import settings
from houses.sheets import (
    col_index,
    sync_view_formulas,
)
from houses.sheets.reader import get_properties_data, resolve_tab
from houses.web.api_router import api_router
from houses.web.auth import auth_router, get_session_user
from houses.web.json_utils import asdict_serializable

logger = logging.getLogger(__name__)


def _on_node_refreshed(node):
    """Broadcast per-node update after a genuine value change."""
    asyncio.create_task(_broadcaster_mod._push_node_update(node))


def _deploy_hash() -> str:
    """Return the short git HEAD hash, or ``""`` when git is unavailable."""
    try:
        _hash = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=2)
        if _hash.returncode == 0 and _hash.stdout.strip():
            return _hash.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return ""


def _property_in_sheet(gclient: Any, rid: str) -> bool:
    """True if *rid* already appears in the Properties Data sheet.

    Sheet errors count as "not present" so the upsert can proceed.
    """
    try:
        sh = gclient.open_by_key(settings.sheet_id)
        ws = sh.worksheet("Properties Data")
        return any(row[col_index("Rightmove ID")].strip() == rid for row in ws.get_all_values()[1:])
    # lucidlint: ignore broad-except deliberate broad catch — boundary/fallback per coding-standards.md
    except Exception:
        return False


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
async def _scrape_url(url: str) -> tuple[RightmoveProperty | None, str | None]:
    """Scrape a Rightmove listing; returns ``(result, error)``."""
    try:
        scraped = await get_scrape_fn()(url)
        return scraped, None
    # lucidlint: ignore broad-except boundary — scrape failures return (None, error) instead of raising
    except Exception as e:
        logger.warning("Scrape failed for %s: %s", url, e)
        return None, str(e)


def _seed_dag(rid2: str, enriched: EnrichedProperty) -> bool:
    """Seed the DAG for *rid2*; returns True on success."""
    try:
        prop = PropertyNodes(rid2)
        push_enriched_property(
            rid2,
            enriched,
            {
                "rightmove_address": prop.rightmove_address,
                "rightmove_url": prop.rightmove_url,
                "rightmove_bedrooms": prop.rightmove_bedrooms,
                "rightmove_price": prop.rightmove_price,
                "rightmove_location": prop.rightmove_location,
                "postcode": prop.postcode,
            },
        )
        _sp.get_services().property_registry.register(rid2, prop)
        logger.info("Seeded DAG for %s", rid2)
        return True
    # lucidlint: ignore broad-except boundary — DAG-seeding failures return False instead of raising
    except Exception as e:
        logger.warning("Failed to seed DAG for %s: %s", rid2, e)
        return False


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # The app-process marker for the settings-write guard: every real
    # worker runs the lifespan; ad-hoc scripts/REPLs that import the app
    # modules do not, so they cannot silently write settings.
    set_app_mode()
    level = logging.DEBUG if settings.trace else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # Log the commit hash so we know what code is running.
    # Gracefully handle environments without git.
    deploy_hash = _deploy_hash()
    if deploy_hash:
        logger.info("Deploy: %s", deploy_hash)
    else:
        logger.info("Deploy: unknown (no git)")

    if settings.trace:
        logging.getLogger("houses.enricher").setLevel(logging.DEBUG)
        logging.getLogger("houses.server").setLevel(logging.DEBUG)
    # httpx logs full URLs including query params — suppress to avoid
    # leaking API keys in the server log
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not settings.web_client_id:
        logger.error("HOUSES_GOOGLE_WEB_CLIENT_ID is not set. Configure Google OAuth in .env.")
        raise RuntimeError("Authentication not configured")
    if not settings.web_client_secret:
        logger.error("HOUSES_GOOGLE_WEB_CLIENT_SECRET is missing. Set it in .env.")
        raise RuntimeError("Google Client Secret not configured")
    if not settings.session_secret:
        logger.error("HOUSES_SESSION_SECRET is empty. Set a non-empty value in .env.")
        raise RuntimeError("Session secret not configured")

    init_dag_db()
    init_app_db()
    _services_mod._reset_settings_cache()
    _property_registry._reset()
    _broadcaster_mod._reset()
    _town_desc._reset()

    if property_rids():
        load_property_nodes_from_db()
    else:
        # Cold start: seed from the Google Sheet
        rows = get_properties_data()
        load_property_nodes_from_rows(rows)
    # Start the background stale-node processor and the WebSocket broadcaster.
    # The processor eagerly recomputes nodes whose dependencies have changed;
    # the broadcaster pushes fresh property summaries to connected clients.
    set_after_refresh(_on_node_refreshed)
    _proc_task = _start_processor()
    _bc_task = asyncio.create_task(_broadcaster_mod._broadcaster())

    logger.info("Houses server starting" + (" (TRACE enabled)" if settings.trace else ""))
    yield
    _proc_task.cancel()
    _bc_task.cancel()
    logger.info("Houses server shutting down")

    close_app_db()
    await stop_chrome()


app = FastAPI(
    title="Houses — Property Enrichment Engine",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "").split(",") if os.environ.get("CORS_ORIGINS") else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.mount("/static", StaticFiles(directory="houses/static"), name="static")
app.include_router(admin_router)
app.include_router(api_router)
app.include_router(auth_router)


@app.middleware("http")
async def _request_context(request, call_next):
    """Set up per-request context (services container) and require auth
    on all /api/* routes except /api/auth/*.

    Only sets the services default if nothing is already in context —
    tests that pre-inject custom services via ``_sp.set()`` keep their
    override.
    """
    existing = _sp._request_services.get()
    if existing is None:
        svc_token = _sp._request_services.set(Services())
        try:
            return await _require_auth(request, call_next)
        finally:
            _sp._request_services.reset(svc_token)
    else:
        return await _require_auth(request, call_next)


async def _require_auth(request, call_next):
    """Require authentication on /api/* routes (except /api/auth/*, /api/ws, OPTIONS)."""
    path = request.url.path
    if path.startswith("/api/") and not path.startswith("/api/auth/") and not path.startswith("/api/ws"):
        if request.method == "OPTIONS":
            return await call_next(request)

        session_user = get_session_user(request)
        if session_user is None:
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})
    return await call_next(request)


@app.get("/properties")
async def list_properties(tab: str = Query(description="Tab: 'view' or 'data'")):
    """List all properties.

    Query parameters:
    - **tab** (required): ``"view"`` or ``"data"``.
    """
    resolve_tab(tab)
    props = get_properties_data()
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    return {"tab": tab, "properties": props}


def _duplicate_error(payload, rid: str, fields) -> JSONResponse | None:
    """A 400 response when the property already exists; None to proceed.

    The DATABASE is the source of truth for duplicates (a re-added
    Rightmove URL must not create a second property), with the sheet as a
    secondary guard.
    """
    if fields or not rid:
        return None
    if rid in property_rids():
        return JSONResponse(
            content={
                "status": "error",
                "error": f"Property {rid} already exists. Use fields= to re-enrich specific fields.",
            },
            status_code=400,
        )
    gclient = get_client_factory()()
    if gclient and settings.sheet_id and _property_in_sheet(gclient, rid):
        return JSONResponse(
            content={
                "status": "error",
                "error": f"Property {rid} already exists. Use fields= to re-enrich specific fields.",
            },
            status_code=400,
        )
    return None


async def _scrape_for_address(payload):
    """Fill address/postcode/bedrooms/price from the scraper when the
    payload lacks an address; returns (scraped, scrape_error, address)."""
    address = payload.address
    if address or not payload.url:
        return None, None, address
    scraped, scrape_error = await _scrape_url(payload.url)
    if not scraped:
        return scraped, scrape_error, address
    if scraped.address:
        address = scraped.address
    if scraped.postcode and not payload.postcode:
        payload.postcode = scraped.postcode
    if scraped.bedrooms is not None and payload.bedrooms is None:
        payload.bedrooms = scraped.bedrooms
    if scraped.price is not None and payload.price is None:
        payload.price = Money(str(scraped.price), "GBP")
    return scraped, scrape_error, address


def _enriched_bedrooms(payload, scraped) -> int:
    """Bedrooms for the seed: payload value, else the scraped one, else 0."""
    if payload.bedrooms is not None:
        return payload.bedrooms
    if scraped and scraped.bedrooms is not None:
        return scraped.bedrooms
    return 0


def _enriched_price(payload, scraped) -> Money:
    """Price for the seed: payload value, else the scraped one, else £0."""
    if payload.price is not None:
        return payload.price
    if scraped and scraped.price is not None:
        return Money(str(scraped.price), "GBP")
    return Money(amount="0", currency="GBP")


def _build_enriched(payload, scraped, address: str, postcode: str) -> EnrichedProperty:
    """Seed the DAG — a fresh enrichment with no sheet writes."""
    return EnrichedProperty(
        url=payload.url or (scraped.url if scraped else ""),
        address=address or (scraped.address if scraped else ""),
        postcode=postcode or (scraped.postcode if scraped else ""),
        bedrooms=_enriched_bedrooms(payload, scraped),
        price=_enriched_price(payload, scraped),
        approx_latitude=scraped.latitude if scraped else None,
        approx_longitude=scraped.longitude if scraped else None,
    )


@app.post("/api/properties", response_model=None)
async def upsert_property(
    payload: Property | None = None,
    no_write: bool = Query(default=False),
    fields: Annotated[list[str] | None, Query()] = None,
    rids: Annotated[str | None, Query()] = None,
    force: bool = Query(default=False),
) -> JSONResponse | StreamingResponse:
    """Upsert a property — enrich it and write to the sheet.

    Two modes:
    1. **Single property** — provide a JSON body with url/address/postcode.
    2. **Batch re-enrich** — use query params ``rids``, ``fields``, ``no_write``.

    Always runs enrichment. Use ``no_write=true`` to cache results without
    writing to the sheet.
    """
    if not payload:
        # No payload — the batch/backfill call is a no-op that must still
        # answer 200 (legacy endpoint contract; see TestBackfillView).
        return JSONResponse(content=None)

    # ── Single property mode ───────────────────────────────────
    postcode = payload.postcode or extract_postcode(payload.address)
    address = payload.address
    rid = payload.rid or RightmoveProperty.rid_from_url(payload.url)

    duplicate = _duplicate_error(payload, rid, fields)
    if duplicate is not None:
        return duplicate

    scraped, scrape_error, address = await _scrape_for_address(payload)
    postcode = payload.postcode or extract_postcode(address)

    # ── Seed the DAG (no sheet writes, no old enrichment) ─────────
    enriched = _build_enriched(payload, scraped, address, postcode)
    rid2 = rid or enriched.rid
    if rid2:
        _seed_dag(rid2, enriched)

    dump = asdict_serializable(enriched)
    extra: dict[str, Any] = {}
    if scrape_error:
        extra["scrape_warning"] = scrape_error
        dump["_scrape_warning"] = scrape_error
    return JSONResponse(content={"status": "ok", "rid": rid2, "data": dump, **extra}, status_code=200)


@app.post("/sync-view-formulas")
async def sync_view_formulas_endpoint() -> JSONResponse:
    """Refresh View tab formulas and named ranges to match the current Data tab."""
    if not settings.sheet_id:
        return JSONResponse(content={"status": "ok", "note": "Sheets not configured"})
    gclient = get_client_factory()()
    if gclient is None:
        return JSONResponse(content={"status": "ok", "note": "Sheets not configured"})
    try:
        sh = gclient.open_by_key(settings.sheet_id)
        sync_view_formulas(sh)
        logger.info("View formulas synced")
        return JSONResponse(content={"status": "ok", "message": "View formulas synced"})
    # lucidlint: ignore broad-except endpoint boundary — any sync failure returns a 500 JSON response, never raises
    except Exception as exc:
        logger.error("Failed to sync view formulas: %s", exc)
        return JSONResponse(content={"status": "error", "error": str(exc)}, status_code=500)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(content={"status": "ok"})
