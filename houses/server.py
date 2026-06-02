"""FastAPI app — /inject-property endpoint, startup/shutdown."""

import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from houses.enricher import (
    compute_lorena_commute,
    compute_petrol_cost,
    compute_simon_commute,
    find_nearest_boys_primary,
    find_nearest_boys_secondary,
)
from houses.models import EnrichedProperty, PropertyPayload
from houses.sheets import write_enriched_row

logger = logging.getLogger(__name__)

# UK postcode patterns
# Full: "RG14 1AA", "SW1A 1AA", "EC3A 7LP"
# Outcode (partial): "RG14", "SW1A", "SL6"
_FULL_POSTCODE_RE = re.compile(
    r"[A-Z]{1,2}[0-9][A-Z0-9]? ?[0-9][A-Z]{2}",
    re.IGNORECASE,
)
_OUTCODE_RE = re.compile(
    r"\b[A-Z]{1,2}[0-9][A-Z0-9]?\b",
    re.IGNORECASE,
)


def extract_postcode(address: str) -> str:
    """Extract the best postcode from an address string.

    Tries full postcode first (e.g. "SL6 1AA"), then falls back to
    outcode only (e.g. "SL6"). Returns empty string if nothing found.
    """
    m = _FULL_POSTCODE_RE.search(address)
    if m:
        return m.group(0).strip().upper()
    m = _OUTCODE_RE.search(address)
    if m:
        return m.group(0).strip().upper()
    return ""


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # httpx logs full URLs including query params — suppress to avoid
    # leaking API keys in the server log
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logger.info("Houses server starting")
    yield
    logger.info("Houses server shutting down")


app = FastAPI(
    title="Houses — Property Enrichment Engine",
    version="0.1.0",
    lifespan=lifespan,
)


@app.post("/inject-property")
async def inject_property(payload: PropertyPayload) -> JSONResponse:
    if not payload.url.startswith("https://www.rightmove.co.uk/"):
        raise HTTPException(status_code=400, detail="URL must be a Rightmove listing")

    postcode = payload.postcode or extract_postcode(payload.address)

    logger.info(
        "Processing: %s | address=%s | postcode=%s | beds=%s | price=%s",
        payload.url,
        payload.address,
        postcode,
        payload.bedrooms,
        payload.price,
    )

    simon = await compute_simon_commute(postcode)
    lorena = await compute_lorena_commute(postcode)
    petrol = await compute_petrol_cost(postcode)
    primary = await find_nearest_boys_primary(postcode, payload.address)
    secondary = await find_nearest_boys_secondary(postcode, payload.address)

    enriched = EnrichedProperty(
        url=payload.url,
        address=payload.address,
        postcode=postcode,
        bedrooms=payload.bedrooms or 0,
        price=payload.price or 0.0,
        simon_commute=simon,
        lorena_commute=lorena,
        petrol=petrol,
        primary_school=primary,
        secondary_school=secondary,
    )

    row_url = await write_enriched_row(enriched)

    if row_url:
        logger.info("Written to sheet: %s", row_url)
        return JSONResponse(
            content={"status": "ok", "row_url": row_url},
            status_code=201,
        )

    return JSONResponse(
        content={"status": "ok", "note": "Sheets not configured", "data": enriched.model_dump(mode="json")},
        status_code=200,
    )


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(content={"status": "ok"})
