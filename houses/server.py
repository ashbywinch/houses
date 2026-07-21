"""FastAPI app — /inject-property endpoint, startup/shutdown."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import houses.services_provider as _sp
from dag.persistence import init_db as init_dag_db
from houses.config import settings
from houses.location import extract_postcode, is_outcode
from houses.nodes.bootstrap import seed_registry_from_sheet
from houses.nodes.cutover import push_enriched_property
from houses.nodes.property import PropertyNodes
from houses.property import Property
from houses.property_registry import register_property
from houses.rightmove_scraper import RightmoveProperty, stop_chrome
from houses.rightmove_scraper import scrape as scrape_rightmove
from houses.services import Services
from houses.sheets import (
    col_index,
    get_client,
    sync_view_formulas,
)
from houses.sheets.reader import get_properties_data, resolve_tab
from houses.web.api_router import api_router
from houses.web.json_utils import asdict_serializable

logger = logging.getLogger(__name__)

def _on_node_refreshed(node):
    """Broadcast per-node update after a genuine value change."""
    import asyncio

    from houses.web.broadcaster import _push_node_update
    asyncio.create_task(_push_node_update(node))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    level = logging.DEBUG if settings.trace else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # Log the commit hash so we know what code is running.
    # Gracefully handle environments without git.
    try:
        import subprocess as _sp

        _hash = _sp.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=2)
        if _hash.returncode == 0 and _hash.stdout.strip():
            logger.info("Deploy: %s", _hash.stdout.strip())
    except Exception:
        logger.info("Deploy: unknown (no git)")

    if settings.trace:
        logging.getLogger("houses.enricher").setLevel(logging.DEBUG)
        logging.getLogger("houses.server").setLevel(logging.DEBUG)
    # httpx logs full URLs including query params — suppress to avoid
    # leaking API keys in the server log
    logging.getLogger("httpx").setLevel(logging.WARNING)
    init_dag_db()
    from houses.council_tax import _reset as _reset_council_tax
    from houses.property_registry import _reset as _reset_property_registry
    from houses.services import _reset_settings_cache
    from houses.town_desc import _reset as _reset_town_desc
    from houses.web.broadcaster import _reset as _reset_broadcaster
    _reset_settings_cache()
    _reset_property_registry()
    _reset_broadcaster()
    _reset_town_desc()
    _reset_council_tax()

    seed_registry_from_sheet()
    # Start the background stale-node processor and the WebSocket broadcaster.
    # The processor eagerly recomputes nodes whose dependencies have changed;
    # the broadcaster pushes fresh property summaries to connected clients.
    from dag.derived_node import set_after_refresh
    from dag.derived_node import start_processor as _start_processor
    from houses.web.broadcaster import _broadcaster as _start_broadcaster

    set_after_refresh(_on_node_refreshed)
    _proc_task = _start_processor()
    _bc_task = asyncio.create_task(_start_broadcaster())

    logger.info("Houses server starting" + (" (TRACE enabled)" if settings.trace else ""))
    yield
    _proc_task.cancel()
    _bc_task.cancel()
    logger.info("Houses server shutting down")
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
app.include_router(api_router)


@app.middleware("http")
async def _request_context(request, call_next):
    """Set up per-request context (services container)."""
    svc_token = _sp._request_services.set(Services())
    try:
        return await call_next(request)
    finally:
        _sp._request_services.reset(svc_token)


@app.get("/properties")
async def list_properties(tab: str = Query(description="Tab: 'view' or 'data'")):
    """List all properties.

    Query parameters:
    - **tab** (required): ``"view"`` or ``"data"``.
    """
    resolve_tab(tab)
    props = get_properties_data()
    return {"tab": tab, "properties": props}


@app.post("/properties", response_model=None)
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
    if payload:
        # ── Single property mode ───────────────────────────────────
        postcode = payload.postcode or extract_postcode(payload.address)
        payload.address if is_outcode(postcode) else postcode
        address = payload.address

        # Check for existing
        rid = payload.rid or RightmoveProperty.rid_from_url(payload.url)
        if not fields and rid:
            gclient = get_client()
            if gclient and settings.sheet_id:
                try:
                    sh = gclient.open_by_key(settings.sheet_id)
                    ws = sh.worksheet("Properties Data")
                    if any(row[col_index("Rightmove ID")].strip() == rid for row in ws.get_all_values()[1:]):
                        return JSONResponse(
                            content={
                                "status": "error",
                                "error": f"Property {rid} already exists. Use fields= to re-enrich specific fields.",
                            },
                            status_code=400,
                        )
                except Exception:
                    pass

        scrape_error = None
        scraped = None
        if not address and payload.url:
            try:
                scraped = await scrape_rightmove(payload.url)
                if scraped:
                    if scraped.address:
                        address = scraped.address
                    if scraped.postcode and not payload.postcode:
                        payload.postcode = scraped.postcode
                    if scraped.bedrooms is not None and payload.bedrooms is None:
                        payload.bedrooms = scraped.bedrooms
                    if scraped.price is not None and payload.price is None:
                        payload.price = scraped.price
                    postcode = payload.postcode or extract_postcode(address)
                    address if is_outcode(postcode) else postcode
            except Exception as e:
                scrape_error = str(e)
                logger.warning("Scrape failed for %s: %s", payload.url, e)

        # ── Seed the DAG (no sheet writes, no old enrichment) ─────────
        from houses.property import EnrichedProperty

        enriched = EnrichedProperty(
            url=payload.url or (scraped.url if scraped else ""),
            address=address or (scraped.address if scraped else ""),
            postcode=postcode or (scraped.postcode if scraped else ""),
            bedrooms=payload.bedrooms if payload.bedrooms is not None else (scraped.bedrooms if scraped else None),
            price=payload.price if payload.price is not None else (scraped.price if scraped else None),
            approx_latitude=scraped.latitude if scraped else None,
            approx_longitude=scraped.longitude if scraped else None,
        )
        rid2 = rid or enriched.rid
        if rid2:
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
                    },
                )
                register_property(rid2, prop)
                logger.info("Seeded DAG for %s", rid2)
            except Exception as e:
                logger.warning("Failed to seed DAG for %s: %s", rid2, e)

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
    gclient = get_client()
    if gclient is None:
        return JSONResponse(content={"status": "ok", "note": "Sheets not configured"})
    try:
        sh = gclient.open_by_key(settings.sheet_id)
        sync_view_formulas(sh)
        logger.info("View formulas synced")
        return JSONResponse(content={"status": "ok", "message": "View formulas synced"})
    except Exception as exc:
        logger.error("Failed to sync view formulas: %s", exc)
        return JSONResponse(content={"status": "error", "error": str(exc)}, status_code=500)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(content={"status": "ok"})
