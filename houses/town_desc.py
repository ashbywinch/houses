import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import httpx

from dag.attempt import Attempt
from houses.api_cache import cached_async_client, with_cache
from houses.settings import settings

logger = logging.getLogger(__name__)

# lucidlint: ignore global-state bounded module cache/state — single writer, deliberate
_town_cache: dict[str, str] = {}


def _reset():
    """Clear the town description cache for test isolation."""
    _town_cache.clear()


API_URL = "https://openrouter.ai/api/v1/chat/completions"


async def generate_town_description(
    town_name: str,
    postcode: str,
    *,
    client_factory: Callable[..., AbstractAsyncContextManager[Any]] | None = None,
    with_cache_fn: Callable[..., Awaitable[dict[str, Any]]] | None = None,
) -> Attempt[str]:
    """Generate a one-sentence neighbourhood description via the LLM API.

    ``client_factory`` and ``with_cache_fn`` are test seams defaulting to
    the module implementations, so tests never monkeypatch module globals.
    """
    key = town_name.strip().lower()
    if key in _town_cache:
        return Attempt.succeeded(_town_cache[key])

    client_factory = client_factory or cached_async_client
    with_cache_fn = with_cache_fn or with_cache

    try:
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
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
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
                {
                    "role": "user",
                    "content": f"{town_name}, {postcode}",
                },
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
                {
                    "role": "user",
                    "content": f"{town_name}, {postcode}.",
                },
            ],
            "max_tokens": settings.llm_max_tokens,
            "temperature": settings.llm_temperature,
        }

        async def _fetch():
            async with client_factory(timeout=15.0) as client:
                resp = await client.post(
                    API_URL,
                    json=body,
                    headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                )
            assert isinstance(resp, httpx.Response)
            resp.raise_for_status()
            return resp.json()

        result = await with_cache_fn("POST", API_URL, body=body, fetch=_fetch)
        raw = result["choices"][0]["message"]["content"].strip()
        description = raw.split(".")[0].strip() + "."
        _town_cache[key] = description
        return Attempt.succeeded(description)
    except (httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException):
        raise  # transient — let DAG retry handle it
    # lucidlint: ignore broad-except boundary — unknown generation failures convert to an impossible attempt
    except Exception as e:
        logger.warning("Failed to generate town description for %s", town_name, exc_info=True)
        return Attempt.impossible(f"town description generation failed: {e}")
