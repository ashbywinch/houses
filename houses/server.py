"""FastAPI app — /inject-property endpoint, startup/shutdown."""

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from houses.enricher import (
    compute_petrol_cost,
    compute_simon_commute,
    compute_lorena_commute,
    find_nearest_boys_primary,
    find_nearest_boys_secondary,
)
from houses.models import EnrichedProperty, PropertyPayload
from houses.sheets import write_enriched_row

app = FastAPI(
    title="Houses — Property Enrichment Engine",
    version="0.1.0",
)


@app.on_event("startup")
async def startup() -> None:
    """Initialise connections on server start."""
    # TODO: authenticate gspread client, warm caches


@app.on_event("shutdown")
async def shutdown() -> None:
    """Clean up on server stop."""
    # TODO: close any open connections


@app.post("/inject-property")
async def inject_property(payload: PropertyPayload) -> JSONResponse:
    """Receive a property payload, enrich it, and write to Google Sheets.

    Expected payload (from Page Assist / browser AI):
        { "url": "...", "postcode": "...", "bedrooms": N, "price": N }
    """
    # --- Validate ---
    if not payload.url.startswith("https://www.rightmove.co.uk/"):
        raise HTTPException(status_code=400, detail="URL must be a Rightmove listing")

    # --- Enrich in parallel ---
    simon = await compute_simon_commute(payload.postcode)
    lorena = await compute_lorena_commute(payload.postcode)
    petrol = compute_petrol_cost(payload.postcode)
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

    # --- Persist ---
    row_url = await write_enriched_row(enriched)

    if row_url:
        return JSONResponse(
            content={"status": "ok", "row_url": row_url},
            status_code=201,
        )

    # Sheets not configured — return enriched data directly
    return JSONResponse(
        content={"status": "ok", "note": "Sheets not configured", "data": enriched.model_dump(mode="json")},
        status_code=200,
    )


@app.get("/health")
async def health() -> JSONResponse:
    """Simple health-check endpoint."""
    return JSONResponse(content={"status": "ok"})
