from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from houses.web.card_data import get_all_cards

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="houses/templates")

web_router = APIRouter()


@web_router.get("/", response_class=HTMLResponse)
async def property_list(request: Request):
    cards = get_all_cards()

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
