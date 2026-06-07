import logging

import httpx

from houses.api_cache import with_cache
from houses.config import settings
from houses.retry import retry_async

logger = logging.getLogger(__name__)

_town_cache: dict[str, str] = {}

API_URL = "https://openrouter.ai/api/v1/chat/completions"


async def generate_town_description(town_name: str, postcode: str) -> str:
    if not settings.llm_api_key:
        logger.warning("llm_api_key not set — skipping town description")
        return ""

    key = town_name.strip().lower()
    if key in _town_cache:
        return _town_cache[key]

    try:
        body = {
            "model": settings.llm_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You describe a UK neighbourhood for someone choosing where to buy a home."
                        " Exactly ONE sentence — no more. Never list multiple areas."
                        " Be specific and balanced: mention character and notable trade-offs"
                        " (lively vs quiet, polished vs gritty, green vs urban, practical vs characterful)."
                        " Differentiate it from other places. No marketing fluff."
                        " Do NOT mention: prices, transport links, commute times, or schools (separate columns)."
                        " Do not start by repeating the area name."
                    ),
                },
                {
                    "role": "user",
                    "content": f"{town_name}, {postcode}",
                },
                {
                    "role": "user",
                    "content": f"{town_name}, {postcode}.",
                },
            ],
            "max_tokens": settings.llm_max_tokens,
            "temperature": settings.llm_temperature,
        }

        async def _fetch():
            resp: object = await retry_async(
                lambda: httpx.AsyncClient(timeout=15.0).post(
                    API_URL,
                    json=body,
                    headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                ),
                max_retries=2,
                base_delay=1.0,
            )
            assert isinstance(resp, httpx.Response)
            resp.raise_for_status()
            return resp.json()

        result = await with_cache("POST", API_URL, body=body, fetch=_fetch)
        raw = result["choices"][0]["message"]["content"].strip()
        description = raw.split(".")[0].strip() + "."
        _town_cache[key] = description
        return description
    except Exception:
        logger.warning("Failed to generate town description for %s", town_name, exc_info=True)
        return ""
