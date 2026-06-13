"""FastAPI app — /inject-property endpoint, startup/shutdown."""

import json
import logging
import re
from collections.abc import AsyncGenerator
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
from houses.property import EnrichedProperty, Property
from houses.rightmove_scraper import scrape as scrape_rightmove
from houses.rightmove_scraper import stop_chrome
from houses.sheets import (
    Tab,
    _rightmove_id,
    col_index,
    col_letter,
    get_client,
    row_values,
    sync_view_formulas,
    write_enriched_row,
)

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


def _get_properties_data() -> list[dict[str, str]]:
    """Read all properties from the Data tab and return them as dicts."""
    from houses.sheets import get_client

    client = get_client()
    if not client:
        return []
    try:
        sh = client.open_by_key(settings.sheet_id)
        ws = sh.worksheet("Properties Data")
        all_rows = ws.get_all_values()
        headers = all_rows[0]
        return [dict(zip(headers, row, strict=False)) for row in all_rows[1:] if row and row[0].strip()]
    except Exception as e:
        logger.warning("Failed to read properties data: %s", e)
        return []


VALID_TABS = {"view", "data"}


def _resolve_tab(tab: str) -> str:
    """Validate *tab* and return ``"Properties View"`` or ``"Properties Data"``."""
    t = tab.strip().lower()
    if t not in VALID_TABS:
        raise ValueError(f"Invalid tab '{tab}'. Must be one of: {', '.join(sorted(VALID_TABS))}")
    return "Properties View" if t == "view" else "Properties Data"


@app.get("/properties")
async def list_properties(tab: str = Query(description="Tab: 'view' or 'data'")):
    """List all properties.

    Query parameters:
    - **tab** (required): ``"view"`` or ``"data"``.
    """
    _resolve_tab(tab)
    props = _get_properties_data()
    return {"tab": tab, "properties": props}


@app.get("/properties/{rid}")
async def get_property(rid: str, tab: str = Query(description="Tab: 'view' or 'data'")):
    """Get a single property by Rightmove ID.

    Detects duplicate RIDs in the sheet and returns a clear error.

    Query parameters:
    - **tab** (required): ``"view"`` or ``"data"``.
    """
    _resolve_tab(tab)
    matches = [p for p in _get_properties_data() if p.get("Rightmove ID", "").strip() == rid]
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
        rid_match = re.search(r"properties/(\d+)", payload.url)
        rid = rid_match.group(1) if rid_match else ""
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
                if scraped.get("address"):
                    address = scraped["address"]
                if scraped.get("postcode") and not payload.postcode:
                    payload.postcode = scraped["postcode"]
                if scraped.get("bedrooms") is not None and payload.bedrooms is None:
                    payload.bedrooms = scraped["bedrooms"]
                if scraped.get("price") is not None and payload.price is None:
                    payload.price = scraped["price"]
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

    return StreamingResponse(_batch_stream(gclient, no_write, fields, rids, force), media_type="text/plain")


async def _batch_stream(
    gclient: Any,
    no_write: bool,
    fields: list[str] | None,
    rids: str | None,
    force: bool = False,
) -> AsyncGenerator[str, None]:
    """Backfill enrichment: read View tab, enrich missing fields, yield NDJSON."""
    try:
        sh = gclient.open_by_key(settings.sheet_id)
        view_ws = sh.worksheet("Properties View")
        data_ws = sh.worksheet("Properties Data")
    except Exception as exc:
        logger.error("Failed to open sheet: %s", exc)
        yield json.dumps({"status": "error", "error": str(exc)}) + "\n"
        return

    view_data = view_ws.get_all_values()
    if len(view_data) < 2:
        yield json.dumps({"status": "ok", "message": "View tab is empty", "results": []}) + "\n"
        return

    view_headers = view_data[0]
    vh = {h.strip().lower(): i for i, h in enumerate(view_headers)}
    url_col = vh.get("rightmove link")
    id_col = vh.get("rightmove id")
    addr_col = vh.get("listing address")

    data_all = data_ws.get_all_values()
    data_headers = data_all[0] if data_all else []

    try:
        data_rid_idx = data_headers.index("Rightmove ID")
    except ValueError:
        data_rid_idx = -1

    user_cols = frozenset({"Actual Latitude", "Actual Longitude"})
    enriched_col_indices: dict[str, int] = {}
    for i, h in enumerate(data_headers):
        if h not in user_cols:
            enriched_col_indices[h] = i

    user_fields: set[str] | None = None
    if fields:
        user_fields = set()
        for f in fields:
            for part in f.split(","):
                p = part.strip()
                if p:
                    user_fields.add(p)
    target_rids: set[str] = {r.strip() for r in rids.split(",")} if rids else set()
    processed_rids: set[str] = set()
    total = len(view_data) - 1
    summary: dict[str, int] = {"updated": 0, "skipped": 0, "created": 0, "errors": 0}

    yield _json_line({"type": "start", "total": total, "no_write": no_write, "force": force, "rids": rids})

    from houses.sheets import row_values as _row_values

    for row_idx, view_row in enumerate(view_data[1:], 2):
        url_raw = view_row[url_col].strip() if url_col is not None and url_col < len(view_row) else ""
        raw_id = view_row[id_col].strip() if id_col is not None and id_col < len(view_row) else ""
        rid = _rightmove_id(raw_id) if raw_id else _rightmove_id(url_raw)
        if not rid:
            summary["skipped"] += 1
            yield _json_line({"type": "row", "row": row_idx, "rid": None, "status": "skipped", "reason": "no RID"})
            continue
        if target_rids and rid not in target_rids:
            summary["skipped"] += 1
            yield _json_line({"type": "row", "row": row_idx, "rid": rid, "status": "skipped", "reason": "not in rids"})
            continue
        if rid in processed_rids:
            summary["skipped"] += 1
            yield _json_line({"type": "row", "row": row_idx, "rid": rid, "status": "skipped", "reason": "duplicate"})
            continue
        processed_rids.add(rid)

        url = url_raw if url_raw.startswith("http") else f"https://www.rightmove.co.uk/properties/{rid}"
        address = view_row[addr_col].strip() if addr_col is not None and addr_col < len(view_row) else ""
        data_row = data_all[row_idx - 1] if len(data_all) > row_idx - 1 else [""] * len(data_headers)
        existing_rid = data_row[data_rid_idx].strip() if data_rid_idx >= 0 and len(data_row) > data_rid_idx else ""
        data_row_num = row_idx if (existing_rid == rid or not existing_rid) else 0

        if data_row_num == 0:
            new_pc = extract_postcode(address) or ""
            enriched = await run_backfill_enrichment(
                url=url,
                address=address,
                postcode=new_pc,
                lookup=None,  # _run_enrichment computes best lookup
                bedrooms=None,
                price=None,
                enabled=None,
            )
            flat = _row_values(enriched)
            if no_write:
                yield _json_line(
                    {
                        "type": "row",
                        "row": row_idx,
                        "rid": rid,
                        "status": "would_create",
                        "enriched": asdict_serializable(enriched),
                        "flat": flat,
                    }
                )
            else:
                row_url_result = await write_enriched_row(enriched)
                summary["created"] += 1
                yield _json_line(
                    {
                        "type": "row",
                        "row": row_idx,
                        "rid": rid,
                        "status": "created",
                        "row_url": row_url_result,
                    }
                )
            continue

        # Decide which columns to consider:
        # - ``fields`` restricts to specific enrichment fields (column groups)
        # - ``force`` controls whether we overwrite existing values or only
        #   fill blank cells
        if user_fields:
            consider_headers = [h for h in data_headers if (ef := header_to_enrichment_field(h)) and ef in user_fields]
        else:
            consider_headers = list(enriched_col_indices.keys())

        if force:
            empty_headers = [h for h in consider_headers if h in enriched_col_indices]
        else:
            empty_headers = [
                h
                for h in consider_headers
                if (ci := enriched_col_indices.get(h)) is not None and (ci >= len(data_row) or not data_row[ci].strip())
            ]

        if not empty_headers:
            summary["skipped"] += 1
            yield _json_line(
                {"type": "row", "row": row_idx, "rid": rid, "status": "skipped", "reason": "already fully enriched"},
            )
            continue

        needed = set()
        for h in empty_headers:
            ef = header_to_enrichment_field(h)
            if ef:
                needed.add(ef)
        if user_fields is not None:
            needed &= user_fields

        addr = data_row[1].strip() if len(data_row) > 1 and data_row[1].strip() else address
        pc = data_row[2].strip() if len(data_row) > 2 and data_row[2].strip() else extract_postcode(addr)
        if "epc" in needed and (not pc or is_outcode(pc)):
            needed.discard("epc")
        if "council_tax" in needed and not addr:
            needed.discard("council_tax")

        if not needed:
            summary["skipped"] += 1
            yield _json_line({"type": "row", "row": row_idx, "rid": rid, "status": "skipped", "reason": "no fields"})
            continue

        yield _json_line({"type": "row", "row": row_idx, "rid": rid, "status": "enriching", "fields": sorted(needed)})
        enriched = await run_backfill_enrichment(
            url=url,
            address=addr,
            postcode=pc,
            lookup=None,  # _run_enrichment computes best lookup (address+postcode upgrade)
            bedrooms=None,
            price=None,
            enabled=needed if needed else None,
        )

        if no_write:
            flat = _row_values(enriched)
            status = "would_create" if not existing_rid else "would_update"
            summary["skipped" if status == "would_update" else "created"] += 1
            yield _json_line(
                {
                    "type": "row",
                    "row": row_idx,
                    "rid": rid,
                    "status": status,
                    "fields": sorted(needed),
                    "enriched": asdict_serializable(enriched),
                    "flat": flat,
                }
            )
        else:
            _write_backfill_cells(
                sh,
                data_ws,
                data_row_num,
                data_headers,
                data_row,
                enriched,
                empty_headers,
                force=force,
                rid=rid,
            )
            yield _json_line({"type": "row", "row": row_idx, "rid": rid, "status": "updated", "fields": sorted(needed)})
            summary["updated"] += 1
        continue

    logger.info(
        "Batch done: %d updated, %d skipped, %d created — %s",
        summary["updated"],
        summary["skipped"],
        summary["created"],
        "force" if force else "blanks only",
    )
    yield _json_line({"type": "summary", **summary})


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
    import csv
    import io

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
    props = _get_properties_data()

    # Build enriched flat dicts by calling _run_backfill_enrichment per property
    enriched_rows: dict[str, dict[str, str]] = {}
    from houses.sheets import row_values

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


def _json_line(data: dict) -> str:
    """Pretty-print JSON for streaming output lines."""
    return json.dumps(data) + "\n"


def _write_backfill_cells(
    sh: Any,
    ws: Any,
    row_num: int,
    headers: list[str],
    current_row: list[str],
    enriched: EnrichedProperty,
    allowed_headers: list[str],
    force: bool = False,
    rid: str = "",
) -> None:
    """Write enriched values to a Data tab row.

    Normally only writes to cells that are currently empty (never overwrites
    existing data). When ``force=True``, writes to all allowed_headers even
    if the cell already has data.
    """
    enriched_dict = row_values(enriched)
    allowed_set = set(allowed_headers)

    cells: list[dict[str, Any]] = []
    written: list[str] = []
    skipped: list[str] = []
    for name, val in enriched_dict.items():
        if not val or name not in allowed_set:
            continue
        try:
            col_idx = headers.index(name)
        except ValueError:
            continue
        if not force and col_idx < len(current_row) and current_row[col_idx].strip():
            skipped.append(name)
            continue  # cell already has data — never overwrite unless force
        cl = col_letter(col_idx)
        cells.append({"range": f"{cl}{row_num}", "values": [[val]]})
        written.append(name)

    if cells:
        Tab(ws).batch_update(cells)
        logger.info(
            "Wrote row %d (RID %s): %d cells [%s]",
            row_num,
            rid,
            len(cells),
            ", ".join(written),
        )
    if skipped:
        logger.info(
            "Skipped row %d (RID %s): %d cells already had data [%s]",
            row_num,
            rid,
            len(skipped),
            ", ".join(skipped),
        )


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(content={"status": "ok"})
