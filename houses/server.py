"""FastAPI app — /inject-property endpoint, startup/shutdown."""

import asyncio
import logging
import os
import subprocess
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from money import Money

import houses.property_registry as _property_registry
import houses.scrape_queue as _scrape_queue
import houses.services as _services_mod
import houses.services_provider as _sp
import houses.town_desc as _town_desc
import houses.web.broadcaster as _broadcaster_mod
from dag.persistence import delete_node_results_for_rid, property_rids
from dag.persistence import init_db as init_dag_db
from dag.scheduler import flush_processor, get_scheduler, set_after_refresh
from dag.scheduler import start_processor as _start_processor
from houses.admin_router import admin_router
from houses.database import close_db as close_app_db
from houses.database import init_db as init_app_db
from houses.location import extract_postcode, upgrade_address
from houses.nodes.bootstrap import load_property_nodes_from_db
from houses.nodes.cutover import push_enriched_property
from houses.nodes.property_nodes import PropertyNodes
from houses.nodes.settings import set_app_mode
from houses.property import EnrichedProperty, Property
from houses.rightmove_scraper import RightmoveProperty, stop_chrome
from houses.services import Services
from houses.settings import settings
from houses.web.api_router import api_router
from houses.web.auth import auth_router, effective_session_user, get_session_user
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



def _seed_dag(rid2: str, enriched: EnrichedProperty) -> bool:
    """Seed the DAG for *rid2*; returns True on success.

    Reuses the registry's existing PropertyNodes when present (re-seeding
    after a scrape report must push onto the SAME nodes — a second
    PropertyNodes instance collides by node-id in the scheduler and its
    derived nodes starve behind the first instance's queued events).
    """
    try:
        registry = _sp.get_services().property_registry
        prop = registry.get(rid2) or PropertyNodes(rid2)
        push_enriched_property(
            rid2,
            enriched,
            # lucidlint: ignore record-shape keyed collection, not a record — source-key → node lookup map
            {
                "rightmove_address": prop.rightmove_address,
                "rightmove_url": prop.rightmove_url,
                "rightmove_bedrooms": prop.rightmove_bedrooms,
                "rightmove_price": prop.rightmove_price,
                "rightmove_location": prop.rightmove_location,
            },
        )
        registry.register(rid2, prop)
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
    # lucidlint: ignore duplicate-block parallel settings-validation guards — each missing setting names its own env
    if not settings.web_client_secret:
        logger.error("HOUSES_GOOGLE_WEB_CLIENT_SECRET is missing. Set it in .env.")
        raise RuntimeError("Google Client Secret not configured")
    if not settings.session_secret:
        logger.error("HOUSES_SESSION_SECRET is empty. Set a non-empty value in .env.")
        raise RuntimeError("Session secret not configured")

    init_dag_db(settings.sqlite_path)
    init_app_db()
    _services_mod._reset_settings_cache()
    _property_registry._reset()
    _broadcaster_mod._reset()
    _town_desc._reset()

    load_property_nodes_from_db()
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
async def list_properties(
    tab: str = Query(default="view", description="Legacy selector ('view'|'data'); both serve the same registry rows"),
):
    """List all properties from the DB-backed DAG registry.

    Each row is a PropertyNodes JSON document (``rid`` plus node JSON for
    best_address / best_location / rightmove_url / rightmove_price /
    rightmove_bedrooms / postcode), sourced from the SQLite database via
    the property registry — the sheet is gone.

    Query parameters:
    - **tab** (legacy): accepted for backwards compatibility; the registry
      is the same regardless of tab.
    """
    props = []
    for rid in _property_registry.list_properties():
        prop = _property_registry.get_property(rid)
        if prop is None:
            continue
        props.append(await prop.to_json())
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    return {"tab": tab, "properties": props}


def _duplicate_error(payload, rid: str, fields) -> JSONResponse | None:
    """A 400 response when the property already exists; None to proceed.

    The DATABASE is the source of truth for duplicates (a re-added
    Rightmove URL must not create a second property).
    """
    if fields or not rid:
        return None
    if rid in property_rids():
        return JSONResponse(
            # lucidlint: ignore record-shape wire-format dict — API response payload, serialization boundary owns the
            content={
                "status": "error",
                "error": f"Property {rid} already exists. Use fields= to re-enrich specific fields.",
            },
            status_code=400,
        )
    return None


async def _scrape_for_address(payload):
    """Fill address/postcode/bedrooms/price from the scraper when the
    payload lacks an address; returns
    ``(scraped, scrape_error, address, scrape_pending)``.

    A URL-only add NEVER blocks on the scrape: the job is enqueued and
    the request returns instantly with ``scrape_pending=True`` — the
    queue + LAN worker complete it (regression: the add used to scrape
    synchronously on hosts with a local scraper, hanging the request for
    the whole fetch). Payloads WITH an address are the user's own facts
    — seeded directly, no scrape, no enqueue.
    """
    address = payload.address
    if address or not payload.url:
        return None, None, address, False
    rid = payload.rid or RightmoveProperty.rid_from_url(payload.url)
    if rid:
        _scrape_queue.enqueue_scrape(rid, payload.url)
    return None, None, address, True


@dataclass(frozen=True)
class SeedFacts:
    """The requester's own facts plus, when one already landed, the
    scraped listing — the two sources the DAG seed reconciles."""

    payload: Property
    scraped: RightmoveProperty | None = None

    def bedrooms(self) -> int | None:
        """Payload bedrooms, else the scraped one.  None when nothing is
        known — the cutover guard skips the push, so a URL-only add never
        displays a made-up 0 (PR #68 review)."""
        if self.payload.bedrooms is not None:
            return self.payload.bedrooms
        return self.scraped.bedrooms if self.scraped else None

    def price(self) -> Money | None:
        """Payload price, else the scraped price as Money.  None when
        nothing is known (cutover guard, PR #68 review)."""
        if self.payload.price is not None:
            return self.payload.price
        if self.scraped and self.scraped.price is not None:
            return Money(str(self.scraped.price), "GBP")
        return None


def _build_enriched(facts: SeedFacts, address: str, postcode: str) -> EnrichedProperty:
    """Seed the DAG — a fresh enrichment."""
    scraped = facts.scraped
    address = address or (scraped.address if scraped else "")
    postcode = postcode or (scraped.postcode if scraped else "")
    if not address and postcode:
        # A known postcode with no address yet (a URL-only add carrying
        # one) must not be lost — seed it as the provisional address so
        # the derivation sees it; the scrape report replaces it with the
        # real address (PR #68 review, data loss).
        address = postcode
    # The postcode node derives from the address — fold a separately-
    # known postcode INTO the address so the derivation sees it (the
    # address stays the single fact).
    address = upgrade_address(address, postcode)
    return EnrichedProperty(
        url=facts.payload.url or (scraped.url if scraped else ""),
        address=address,
        bedrooms=facts.bedrooms(),
        price=facts.price(),
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
    """Upsert a property — enrich it and seed the DAG.

    Two modes:
    1. **Single property** — provide a JSON body with url/address/postcode.
    2. **Batch re-enrich** — use query params ``rids``, ``fields``, ``no_write``.

    Always runs enrichment. Use ``no_write=true`` to cache results without
    persisting to the database.
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
    scraped, scrape_error, address, scrape_pending = await _scrape_for_address(payload)
    postcode = payload.postcode or extract_postcode(address)

    # ── Seed the DAG ─────────────────────────────────────────────
    enriched = _build_enriched(SeedFacts(payload=payload, scraped=scraped), address, postcode)
    rid2 = rid or enriched.rid
    if rid2:
        _seed_dag(rid2, enriched)

    dump = asdict_serializable(enriched)
    # The postcode is no longer an EnrichedProperty field (the address
    # carries it, PostcodeNode derives) — keep it on the wire for
    # clients that read the add response.
    dump["postcode"] = postcode
    extra: dict[str, Any] = {}
    if scrape_error:
        extra["scrape_warning"] = scrape_error
        dump["_scrape_warning"] = scrape_error
    if scrape_pending:
        extra["scrape_pending"] = True
    return JSONResponse(content={"status": "ok", "rid": rid2, "data": dump, **extra}, status_code=200)
def _require_superuser(request: Request) -> None:
    """403 unless the request carries a signed-in superuser session."""
    user = effective_session_user(request)
    if not user or not user.get("is_superuser"):
        raise HTTPException(status_code=403, detail="Superuser access required")


def _job_wire(job):
    """Serialization boundary: a claimed job as the worker sees it."""
    if job is None:
        return None
    # lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
    return {"id": job.id, "rid": job.rid, "url": job.url}
# lucidlint: ignore record-shape wire-format dict — the worker's report body is a wire record (coding-standards.md)
async def _apply_scraped_report(rid: str, data: dict) -> bool:
    """Push a worker's scraped listing into the property's DAG — the same
    seed the sync add path performs, sourced entirely from the report.
    Returns False when the DAG seed fails (the caller re-queues the job)."""
    # Worker fields are scraped text — guard the coercion so a
    # non-parseable value re-queues the job instead of 500ing and
    # leaving it in_progress (PR #68 review).
    try:
        bedrooms = int(data["bedrooms"]) if data.get("bedrooms") is not None else 0
        price = Money(str(data["price"]), "GBP") if data.get("price") is not None else Money(amount="0", currency="GBP")
    except (ValueError, TypeError):
        return False
    enriched = EnrichedProperty(
        url=data.get("url", ""),
        address=upgrade_address(data.get("address", ""), data.get("postcode", "")),
        bedrooms=bedrooms,
        price=price,
        approx_latitude=data.get("latitude"),
        approx_longitude=data.get("longitude"),
    )

    seeded = _seed_dag(rid, enriched)
    # Drain the downstream cascade so the client's immediate refetch
    # reflects the completed enrichment (same pattern as patch_address).
    await flush_processor()
    return seeded

@app.post("/api/scrapes/claim", response_model=None)
async def claim_scrape(request: Request) -> JSONResponse:
    """Claim the oldest due scrape job for the worker (superuser)."""
    _require_superuser(request)
    job = _scrape_queue.claim_due_scrape()
    return JSONResponse(content={"job": _job_wire(job)})


@app.post("/api/scrapes/report", response_model=None)
async def report_scrape(request: Request, body: dict) -> JSONResponse:
    """Worker outcome for a claimed job (superuser).

    ``{"job_id": N, "ok": true, "data": {...}}`` applies the scraped data
    to the property's DAG and deletes the job.  ``{"job_id": N, "ok":
    false, "error": "..."}`` re-queues it with exponential backoff (or
    marks it failed after MAX_ATTEMPTS).
    """
    _require_superuser(request)
    job_id = body.get("job_id")
    if not isinstance(job_id, int):
        raise HTTPException(status_code=422, detail="job_id required")
    if body.get("ok"):
        data = body.get("data") or {}
        # A login wall / block page can parse to an empty address — such a
        # "success" would seed a garbage property. Reject it: the job is
        # re-queued with backoff instead of deleted.
        if not data.get("address"):
            _scrape_queue.report_scrape(job_id, ok=False, error="report missing an address")
            return JSONResponse(content={"status": "ok"})
        rid = _scrape_queue.scrape_job_rid(job_id)
        if rid is None:
            raise HTTPException(status_code=404, detail="unknown job")
        # Apply BEFORE deleting: a failed DAG seed must re-queue the job,
        # not lose the listing forever.
        if await _apply_scraped_report(rid, data):
            _scrape_queue.report_scrape(job_id, ok=True)
        else:
            _scrape_queue.report_scrape(job_id, ok=False, error="DAG seed failed")
        return JSONResponse(content={"status": "ok"})
    _scrape_queue.report_scrape(job_id, ok=False, error=str(body.get("error") or ""))
    return JSONResponse(content={"status": "ok"})


@app.get("/api/scrapes/status", response_model=None)
async def scrape_status(request: Request) -> JSONResponse:
    """Queue depth by status for the operator (superuser)."""
    _require_superuser(request)
    st = _scrape_queue.scrape_queue_status()
    return JSONResponse(
        # lucidlint: ignore record-shape wire-format dict — API response payload, serialization boundary owns the shape
        content={"scrapes": {"pending": st.pending, "in_progress": st.in_progress, "failed": st.failed}}
    )


@app.post("/api/properties/{rid}/scrape/retry", response_model=None)
async def retry_scrape(rid: str) -> JSONResponse:
    """Re-enqueue a property's scrape (the wireframe's Retry on a failed
    card). The queue job is dropped and a fresh one enqueued — the URL
    comes from the property's existing rightmove_url node."""
    prop = _sp.get_services().property_registry.get(rid)
    if prop is None:
        raise HTTPException(status_code=404, detail=f"Property {rid} not found")
    url_attempt = prop.rightmove_url.latest_attempt()
    url = url_attempt.value_or_none() if url_attempt.succeeded else ""
    if not url:
        raise HTTPException(status_code=422, detail="no Rightmove URL on this property")
    _scrape_queue.cancel_scrape_for_rid(rid)
    _scrape_queue.enqueue_scrape(rid, url)
    return JSONResponse(content={"status": "ok"})


@app.patch("/api/properties/{rid}/details", response_model=None)
async def patch_property_details(rid: str, body: dict) -> JSONResponse:
    """'I know the details' — the user's own facts complete the property
    instantly (P3: fix facts, not symptoms) and cancel the scrape job."""
    prop = _sp.get_services().property_registry.get(rid)
    if prop is None:
        raise HTTPException(status_code=404, detail=f"Property {rid} not found")
    address = body.get("address") or ""
    if not address:
        raise HTTPException(status_code=422, detail="address required")
    # Omitted fields stay None — the cutover guards skip them, so the
    # property never displays made-up £0/0 figures (PR #68 review).
    enriched = EnrichedProperty(
        address=address,
        url=(prop.rightmove_url.latest_attempt().value_or_none() or "")
        if prop.rightmove_url.latest_attempt().succeeded
        else "",
        bedrooms=int(body["bedrooms"]) if body.get("bedrooms") is not None else None,
        price=Money(str(body["price"]), "GBP") if body.get("price") is not None else None,
    )
    seeded = _seed_dag(rid, enriched)
    if not seeded:
        raise HTTPException(status_code=500, detail="failed to apply details")
    # Cancel the scrape only when the user's facts are complete — an
    # address-only form must leave the job to fill price/bedrooms.
    if body.get("price") is not None and body.get("bedrooms") is not None:
        _scrape_queue.cancel_scrape_for_rid(rid)
    await flush_processor()
    return JSONResponse(content={"status": "ok"})


def _disconnect_property_nodes(rid: str) -> None:
    """Unregister every node the property registered in the scheduler.

    Remove-then-re-add must not leave orphaned nodes registered under
    the same node ids — their queued events would clobber the re-added
    property's rows and starve its derived nodes (PR #68 review).  The
    scheduler registry is the COMPLETE node set: builder-orphaned
    sub-pipelines (school transit, walk/drive/bus variants that no
    selector references) are not reachable from the property's
    attributes, so a graph walk is not enough.
    """

    for nid, node in list(get_scheduler().registered_nodes().items()):
        if nid.startswith(f"{rid}/"):
            node.disconnect()


@app.delete("/api/properties/{rid}", response_model=None)
async def remove_property(rid: str) -> JSONResponse:
    """Remove a property (the wireframe's Remove): the scrape job, the
    DAG rows, and the registry entry all go away."""
    _scrape_queue.cancel_scrape_for_rid(rid)
    _disconnect_property_nodes(rid)
    delete_node_results_for_rid(rid)
    _sp.get_services().property_registry.remove(rid)
    return JSONResponse(content={"status": "ok"})


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(content={"status": "ok"})
