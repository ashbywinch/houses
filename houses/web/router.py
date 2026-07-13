from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from houses.config import settings
from houses.geo import GeoPoint
from houses.model.persistence import insert_source_value, insert_user_input
from houses.model.resolver import check_staleness, resolve_property
from houses.sheets.reader import get_properties_data, resolve_tab
from houses.web.card_data import get_all_cards
from houses.web.geo_utils import valid_location

logger = logging.getLogger(__name__)


def _try_float(val: str | None) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _try_int(val: str | None) -> int | None:
    if val is None:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None

templates = Jinja2Templates(directory="houses/templates")


def _provenance_class(source: str) -> str:
    if not source:
        return ""
    if source.startswith("User") or "Browser" in source:
        return "user"
    if source.startswith("Geocoded"):
        return "geocoding"
    if source.startswith("Rightmove"):
        return "rightmove"
    if source == "Computed":
        return "computed"
    return ""


templates.env.globals["provenance_class"] = _provenance_class

web_router = APIRouter()


async def _try_import_from_sheet(rid: str) -> tuple[bool, list[str]]:
    """Import a property from the Google Sheet into SQLite on first view.

    Reads the Data tab row for *rid* and inserts source_values for any
    columns with data. Returns (imported_anything, warnings).
    """
    if not settings.sheet_id:
        return False, []
    try:
        resolve_tab("data")
        props = get_properties_data()
    except Exception:
        logger.debug("Sheet not available for import of %s", rid)
        return False, []

    match = None
    for p in props:
        if p.get("Rightmove ID", "").strip() == rid:
            match = p
            break
    if not match:
        return False, []

    imported = False
    warnings: list[str] = []

    url = (match.get("Rightmove URL") or "").strip()
    if url:
        insert_source_value(rid, "rightmove_url", url, "Browser extension")
        imported = True

    address = (match.get("Address") or "").strip()
    postcode = (match.get("Postcode") or "").strip()
    bedrooms = (match.get("Bedrooms") or "").strip()
    price = (match.get("Price (£)") or "").strip()

    if address:
        insert_source_value(rid, "rightmove_address", address, "Rightmove")
        imported = True
    if bedrooms:
        insert_source_value(rid, "rightmove_bedrooms", bedrooms, "Rightmove")
        imported = True
    if price:
        insert_source_value(rid, "rightmove_price", price, "Rightmove")
        imported = True

    approx_lat = (match.get("Approx Latitude (est)") or "").strip()
    approx_lng = (match.get("Approx Longitude (est)") or "").strip()
    if approx_lat and approx_lng:
        try:
            flat, flng = float(approx_lat), float(approx_lng)
            postcode = (match.get("Postcode") or "").strip()
            if valid_location(flat, flng, postcode):
                gp = GeoPoint(lat=flat, lon=flng)
                insert_source_value(rid, "rightmove_location", gp, "Rightmove map")
                imported = True
            else:
                msg = (
                    f"Rejected implausible coordinates ({approx_lat}, {approx_lng}) "
                    f"for postcode {postcode}"
                )
                logger.warning("%s: %s", rid, msg)
                warnings.append(msg)
        except (ValueError, TypeError):
            pass

    actual_lat = (match.get("Actual Latitude") or "").strip()
    actual_lng = (match.get("Actual Longitude") or "").strip()
    if actual_lat and actual_lng:
        try:
            gp = GeoPoint(lat=float(actual_lat), lon=float(actual_lng))
            insert_user_input(rid, "precise_location", gp)
            imported = True
        except (ValueError, TypeError):
            pass

    if imported:
        logger.info("Imported property %s from sheet into SQLite", rid)

    return imported, warnings


@web_router.get("/", response_class=HTMLResponse)
async def property_list(request: Request):
    cards = await get_all_cards()

    current_home_total: float | None = None
    for c in cards:
        if c.status == "Current" and c.total_monthly_cost is not None:
            current_home_total = c.total_monthly_cost
            break

    dismissed_count = sum(1 for c in cards if c.status == "No")

    return templates.TemplateResponse(
        request,
        "property_list.html",
        {
            "cards": cards,
            "current_home_total": current_home_total,
            "dismissed_count": dismissed_count,
        },
    )


@web_router.get("/properties/{rid}")
async def property_detail(request: Request, rid: str, tab: str = Query(default="data")):
    wants_html = "text/html" in request.headers.get("accept", "")

    if wants_html:
        _404_ctx = {
            "rid": rid, "found": False, "warnings": [],
            "address": "", "best_address": None, "best_address_source": "",
            "best_location": None, "best_location_source": "",
            "map_url": None, "price": None, "bedrooms": None,
        }
        try:
            results = await resolve_property(rid)
        except Exception:
            logger.exception("Failed to resolve property %s", rid)
            return templates.TemplateResponse(
                request, "property_detail.html", _404_ctx, status_code=404
            )

        if not results or all(r.value is None for r in results.values()):
            imported, import_warnings = await _try_import_from_sheet(rid)
            if imported:
                results = await resolve_property(rid)
        else:
            import_warnings = []

        if not results or all(r.value is None for r in results.values()):
            return templates.TemplateResponse(
                request, "property_detail.html", _404_ctx, status_code=404
            )

        best_address = results.get("best_address")
        best_location = results.get("best_location")
        map_url_result = results.get("map_url")

        return templates.TemplateResponse(
            request,
            "property_detail.html",
            {
                "rid": rid,
                "found": True,
                "warnings": import_warnings,
                "address": best_address.value if best_address else "",
                "best_address": best_address.value if best_address else None,
                "best_address_source": best_address.source if best_address else "",
                "best_location": best_location.value if best_location else None,
                "best_location_source": best_location.source if best_location else "",
                "map_url": map_url_result.value if map_url_result else None,
                "price": _try_float(results.get("rightmove_price").value) if results.get("rightmove_price") else None,
                "bedrooms": _try_int(
                    results.get("rightmove_bedrooms").value
                ) if results.get("rightmove_bedrooms") else None,
            },
        )

    resolve_tab(tab)
    matches = [p for p in get_properties_data() if p.get("Rightmove ID", "").strip() == rid]
    if not matches:
        return JSONResponse({"error": "property not found", "rid": rid}, status_code=404)
    if len(matches) > 1:
        logger.warning(
            "Duplicate RID %s found in %d rows — data may be inconsistent.",
            rid,
            len(matches),
        )
        return JSONResponse(
            {"warning": "duplicate rows", "rid": rid, "count": len(matches)},
            status_code=409,
        )
    return {"tab": tab, **matches[0]}


@web_router.get("/properties/{rid}/edit-address", response_class=HTMLResponse)
async def edit_address(request: Request, rid: str):
    results = await resolve_property(rid, ["best_address", "corrected_address"])
    current = results.get("corrected_address")
    best = results.get("best_address")
    current_value = current.value if current else (best.value if best else "")
    return templates.TemplateResponse(
        request,
        "_edit_address.html",
        {"rid": rid, "current_value": current_value},
    )


@web_router.get("/properties/{rid}/field/address", response_class=HTMLResponse)
async def address_field(request: Request, rid: str):
    results = await resolve_property(rid, ["best_address", "best_address"])
    best = results.get("best_address")
    return templates.TemplateResponse(
        request,
        "_address_field.html",
        {
            "rid": rid,
            "best_address": best.value if best else None,
            "best_address_source": best.source if best else "",
        },
    )


@web_router.get("/properties/{rid}/field/location", response_class=HTMLResponse)
async def location_field(request: Request, rid: str):
    results = await resolve_property(rid, ["best_location"])
    loc = results.get("best_location")
    return templates.TemplateResponse(
        request,
        "_location_field.html",
        {
            "rid": rid,
            "best_location": loc.value if loc else None,
            "best_location_source": loc.source if loc else "",
        },
    )


@web_router.post("/properties/{rid}/enhance", response_class=HTMLResponse)
async def enhance_property(
    request: Request,
    rid: str,
    action: str = Form(...),
    corrected_address: str | None = Form(None),
    precise_lat: float | None = Form(None),
    precise_lng: float | None = Form(None),
):
    if action == "address" and corrected_address and corrected_address.strip():
        insert_user_input(rid, "corrected_address", corrected_address.strip())
        await resolve_property(rid, ["best_address", "best_location", "map_url"])
        return await address_field(request, rid)

    if action == "location" and precise_lat is not None and precise_lng is not None:
        gp = GeoPoint(lat=precise_lat, lon=precise_lng)
        insert_user_input(rid, "precise_location", gp)
        await resolve_property(rid, ["best_address", "best_location", "map_url"])
        return await location_field(request, rid)

    return await property_detail(request, rid)


@web_router.get("/properties/{rid}/staleness", response_class=HTMLResponse)
async def staleness_check(request: Request, rid: str, nodes: str = ""):
    node_list = [n.strip() for n in nodes.split(",") if n.strip()]
    if not node_list:
        node_list = ["best_address", "best_location", "map_url"]
    stale = check_staleness(rid, node_list)
    results = await resolve_property(rid, node_list)
    parts: list[str] = []
    for nid in node_list:
        is_stale = stale.get(nid, True)
        result = results.get(nid)
        if is_stale:
            spinner_hx = (
                f'hx-get="/properties/{rid}/staleness?nodes={nid}"'
                ' hx-trigger="every 3s" hx-swap="outerHTML"'
            )
            parts.append(f'<span class="stale-spinner" id="spinner-{nid}" {spinner_hx}></span>')
        else:
            provenance = result.source if result else ""
            pc = _provenance_class(provenance)
            parts.append(
                f'<span class="stale-spinner--fresh" id="spinner-{nid}">'
                f'<span class="provenance-badge provenance-badge--{pc}">{provenance or "—"}</span></span>'
            )
    return HTMLResponse("".join(parts))


@web_router.get("/properties/{rid}/map-picker", response_class=HTMLResponse)
async def map_picker(request: Request, rid: str):
    results = await resolve_property(rid, ["best_location"])
    loc = results.get("best_location")
    lat = loc.value.lat if loc and loc.value else ""
    lng = loc.value.lon if loc and loc.value else ""
    return templates.TemplateResponse(
        request,
        "_map_picker.html",
        {
            "rid": rid,
            "lat": lat,
            "lng": lng,
        },
    )
