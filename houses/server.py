"""FastAPI app — /inject-property endpoint, startup/shutdown."""

import csv
import io
import json
import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, StreamingResponse

import houses.location as _loc
from houses.config import settings
from houses.enrichment_runner import (
    asdict_serializable,
    extract_postcode,
    header_to_enrichment_field,
    is_outcode,
    run_backfill_enrichment,
    run_enrichment,
)
from houses.property import Property
from houses.rightmove_scraper import RightmoveProperty, stop_chrome
from houses.rightmove_scraper import scrape as scrape_rightmove
from houses.sheets import (
    col_index,
    get_client,
    row_values,
    sync_view_formulas,
    write_enriched_row,
)
from houses.sheets.backfill import batch_stream
from houses.sheets.reader import get_properties_data, resolve_tab

logger = logging.getLogger(__name__)


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
    logger.info("Houses server starting" + (" (TRACE enabled)" if settings.trace else ""))
    yield
    logger.info("Houses server shutting down")
    await stop_chrome()


app = FastAPI(
    title="Houses — Property Enrichment Engine",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def _request_context(request, call_next):
    """Set up per-request context (geo cache, geo state, services, bus fares)."""
    import houses.context as _ctx
    from houses.bus_journey import BusJourneyRegistry
    from houses.location import _geo_state_var, _GeoState
    from houses.services import Services

    geo_cache_token = _loc._geo_cache_var.set({})
    geo_state_token = _geo_state_var.set(_GeoState())
    svc_token = _ctx._request_services.set(Services())
    bus_token = _ctx._request_bus_fares.set(BusJourneyRegistry())
    try:
        return await call_next(request)
    finally:
        _ctx._request_bus_fares.reset(bus_token)
        _ctx._request_services.reset(svc_token)
        _geo_state_var.reset(geo_state_token)
        _loc._geo_cache_var.reset(geo_cache_token)


@app.get("/properties")
async def list_properties(tab: str = Query(description="Tab: 'view' or 'data'")):
    """List all properties.

    Query parameters:
    - **tab** (required): ``"view"`` or ``"data"``.
    """
    resolve_tab(tab)
    props = get_properties_data()
    return {"tab": tab, "properties": props}


@app.get("/properties/{rid}")
async def get_property(rid: str, tab: str = Query(description="Tab: 'view' or 'data'")):
    """Get a single property by Rightmove ID.

    Detects duplicate RIDs in the sheet and returns a clear error.

    Query parameters:
    - **tab** (required): ``"view"`` or ``"data"``.
    """
    resolve_tab(tab)
    matches = [p for p in get_properties_data() if p.get("Rightmove ID", "").strip() == rid]
    if not matches:
        return JSONResponse({"error": "property not found", "rid": rid}, status_code=404)
    if len(matches) > 1:
        logger.warning(
            "Duplicate RID %s found in %d rows — data may be inconsistent. Delete the duplicate row from the sheet.",
            rid,
            len(matches),
        )
        return JSONResponse(
            {
                "warning": "duplicate rows",
                "rid": rid,
                "count": len(matches),
                "message": f"RID {rid} appears in {len(matches)} rows. "
                f"Delete the duplicate row(s) from the sheet and retry.",
            },
            status_code=409,
        )
    return {"tab": tab, **matches[0]}


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
        lookup = payload.address if is_outcode(postcode) else postcode
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
                    lookup = address if is_outcode(postcode) else postcode
            except Exception as e:
                scrape_error = str(e)
                logger.warning("Scrape failed for %s: %s", payload.url, e)

        enabled = set(fields) if fields else None
        enriched = await run_enrichment(
            url=payload.url,
            address=address,
            postcode=postcode,
            lookup=lookup,
            bedrooms=payload.bedrooms,
            price=payload.price,
            enabled=enabled,
            actual_latitude=payload.actual_latitude,
            actual_longitude=payload.actual_longitude,
        )

        row_url = None
        if not no_write:
            row_url = await write_enriched_row(enriched, payload.tab)

        dump = asdict_serializable(enriched)
        extra: dict[str, Any] = {}
        if scrape_error:
            extra["scrape_warning"] = scrape_error
            dump["_scrape_warning"] = scrape_error

        if row_url:
            return JSONResponse(content={"status": "ok", "row_url": row_url, "data": dump, **extra}, status_code=201)
        return JSONResponse(
            content={"status": "ok", "note": "Sheets not configured", "data": dump, **extra}, status_code=200
        )

    # ── Batch mode ────────────────────────────────────────────────
    if not settings.sheet_id:

        async def _empty():
            yield json.dumps({"status": "ok", "note": "Sheets not configured", "results": []}) + "\n"

        return StreamingResponse(_empty(), media_type="text/plain")

    gclient = get_client()
    if gclient is None:

        async def _empty():
            yield json.dumps({"status": "ok", "note": "Sheets not configured", "results": []}) + "\n"

        return StreamingResponse(_empty(), media_type="text/plain")

    return StreamingResponse(batch_stream(gclient, no_write, fields, rids, force), media_type="text/plain")


@app.post("/properties/compare", response_model=None)
async def compare_properties(
    rids: Annotated[str | None, Query()] = None,
    fields: Annotated[list[str] | None, Query()] = None,
) -> StreamingResponse:
    """Compare current sheet data with a fresh no-write re-enrichment.

    Returns a TSV diff with columns RID, Field, Old (sheet), New (enriched).
    This is POST because it triggers enrichment (API calls, caching).

    ``fields`` is a list of column header names to compare (e.g.
    ``["Simon Parking Cost (£)"]``).  Each column header is mapped to
    its enrichment group so only the required API calls are made.
    If omitted, all enrichment columns are compared.
    """

    # Map column headers to enrichment field groups
    enabled_groups: set[str] | None = None
    compare_columns: set[str] | None = None
    if fields:
        enabled_groups = set()
        compare_columns = set()
        for col in fields:
            compare_columns.add(col.strip())
            group = header_to_enrichment_field(col.strip())
            if group:
                enabled_groups.add(group)

    # Read sheet data first
    props = get_properties_data()

    # Build enriched flat dicts by calling _run_backfill_enrichment per property
    enriched_rows: dict[str, dict[str, str]] = {}

    for _view_row_idx, data_row in enumerate([list(p.values()) for p in props], 2):
        rid = data_row[col_index("Rightmove ID")] if col_index("Rightmove ID") < len(data_row) else ""
        if not rid:
            continue
        if rids and rid not in {r.strip() for r in rids.split(",")}:
            continue

        address = data_row[col_index("Address")] if col_index("Address") < len(data_row) else ""
        postcode = data_row[col_index("Postcode")] if col_index("Postcode") < len(data_row) else ""
        url = (
            data_row[col_index("Rightmove URL")]
            if col_index("Rightmove URL") < len(data_row)
            else (f"https://www.rightmove.co.uk/properties/{rid}")
        )

        enriched = await run_backfill_enrichment(
            url=url,
            address=address,
            postcode=postcode,
            lookup=None,  # _run_enrichment will compute best lookup
            bedrooms=None,
            price=None,
            enabled=enabled_groups,
        )
        enriched_rows[rid] = row_values(enriched)

    # Build TSV diff
    output = io.StringIO()
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")
    writer.writerow(["RID", "Field", "Old (sheet)", "New (enriched)"])

    diff_count = 0
    for p in props:
        rid = p.get("Rightmove ID", "").strip()
        if not rid or rid not in enriched_rows:
            continue
        new_data = enriched_rows[rid]
        for header, old_val in p.items():
            stripped = header.strip()
            if not stripped or stripped in ("Rightmove ID",):
                continue
            if compare_columns is not None and stripped not in compare_columns:
                continue
            new_val = new_data.get(stripped, "")
            old_clean = old_val.strip() if old_val else ""
            if old_clean != new_val:
                diff_count += 1
                writer.writerow([rid, stripped, old_clean, new_val])

    writer.writerow([])
    writer.writerow(["DIFF_COUNT", str(diff_count), "", ""])
    result = output.getvalue()

    return StreamingResponse(iter([result]), media_type="text/tab-separated-values")


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
