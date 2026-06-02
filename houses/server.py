"""FastAPI app — /inject-property endpoint, startup/shutdown."""

import logging
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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
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

    logger.info(
        "Processing: %s (%s, %d bed, £%.0f)",
        payload.url, payload.postcode, payload.bedrooms, payload.price,
    )

    simon = await compute_simon_commute(payload.postcode)
    lorena = await compute_lorena_commute(payload.postcode)
    petrol = await compute_petrol_cost(payload.postcode)
    primary = await find_nearest_boys_primary(payload.postcode)
    secondary = await find_nearest_boys_secondary(payload.postcode)

    enriched = EnrichedProperty(
        url=payload.url,
        postcode=payload.postcode,
        bedrooms=payload.bedrooms,
        price=payload.price,
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
