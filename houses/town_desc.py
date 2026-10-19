import logging
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from typing import Any, ClassVar

import httpx

from dag.attempt import Attempt
from houses.api_cache import cached_async_client, with_cache
from houses.settings import settings
from serialization import SerializableAsDict

logger = logging.getLogger(__name__)


class _TownDescGateway:
    """The town-description LLM surface: endpoint path, request headers,
    and the memo cache — one owner; tests clear the cache whole."""

    # ALL LLM access goes through Cloudflare AI Gateway (skill://cloudflare-ai-gateway):
    # the gateway owns the provider keys (BYOK), the text route (`dynamic/fallback2`)
    # and per-repo analytics. The app authenticates with the gateway token and never
    # holds a provider key. The endpoint URL comes from settings.llm_base_url.
    CHAT_COMPLETIONS_PATH = "/chat/completions"

    # Gateway request headers: tag the traffic so Cloudflare's analytics attribute it
    # to this app (the local agent proxy does the same for OMP), and keep retries on
    # the client — the DAG re-raises transient failures, and a gateway retry would
    # pay for the same generation twice (skill://cloudflare-ai-gateway → Retries).
    GATEWAY_HEADERS = {"cf-aig-metadata": '{"source":"app","repo":"houses"}', "cf-aig-max-attempts": "0"}

    _CACHE: ClassVar[dict[str, str]] = {}


def _reset():
    """Clear the town description cache for test isolation."""
    _TownDescGateway._CACHE.clear()


class _ChatMessage(SerializableAsDict):
    """A single message in the OpenRouter chat-completions request body."""

    role: str
    content: str


class _Reasoning(SerializableAsDict):
    """OpenRouter's reasoning control.

    The intended model (``deepseek/deepseek-v4.1-flash``) reasons by default
    and spends the ENTIRE token budget thinking: with this prompt, 150 and
    even 400 max_tokens came back ``finish_reason="length"`` with
    ``content=None`` — i.e. no description at all. Asking for no reasoning
    answers in ~1.5 s with the sentence (measured 2026-10-05).
    """

    enabled: bool


class _ChatBody(SerializableAsDict):
    """The OpenRouter chat-completions request body: one record whose fields ARE
    the provider's keys, serialized once at the HTTP edge by ``to_dict`` (never
    a hand-built dict)."""

    model: str
    messages: list[_ChatMessage]
    max_tokens: int
    temperature: float
    reasoning: _Reasoning


async def generate_town_description(
    town_name: str,
    postcode: str,
    *,
    client_factory: Callable[..., AbstractAsyncContextManager[Any]] | None = None,
    with_cache_fn: Callable[..., Awaitable[Mapping[str, Any]]] | None = None,
) -> Attempt[str]:
    """Generate a one-sentence neighbourhood description via the LLM API.

    ``client_factory`` and ``with_cache_fn`` are test seams defaulting to
    the module implementations, so tests never monkeypatch module globals.
    """
    key = town_name.strip().lower()
    if key in _TownDescGateway._CACHE:
        return Attempt.succeeded(_TownDescGateway._CACHE[key])

    client_factory = client_factory or cached_async_client
    with_cache_fn = with_cache_fn or with_cache

    try:
        body = _ChatBody(
            model=settings.llm_model,
            messages=[
                _ChatMessage(
                    role="system",
                    content=(
                        "You describe a UK neighbourhood for someone choosing where to buy a home."
                        " Exactly ONE sentence — no more. Never list multiple areas."
                        " Be specific and balanced: mention character and notable trade-offs"
                        " (lively vs quiet, polished vs gritty, green vs urban, practical vs characterful)."
                        " Differentiate it from other places. No marketing fluff."
                        " Do NOT mention: prices, transport links, commute times, or schools (separate columns)."
                        " Do not start by repeating the area name."
                    ),
                ),
                _ChatMessage(role="user", content=f"{town_name}, {postcode}"),
                _ChatMessage(role="user", content=f"{town_name}, {postcode}."),
            ],
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
            reasoning=_Reasoning(enabled=False),
        )

        url = f"{settings.llm_base_url}{_TownDescGateway.CHAT_COMPLETIONS_PATH}"

        async def _fetch():
            async with client_factory(timeout=15.0) as client:
                resp = await client.post(
                    url,
                    json=body.to_dict(),
                    headers={
                        "Authorization": f"Bearer {settings.llm_api_key}",
                        **(_TownDescGateway.GATEWAY_HEADERS),
                    },
                )
            assert isinstance(resp, httpx.Response)
            resp.raise_for_status()
            return resp.json()

        result = await with_cache_fn("POST", url, body=body, fetch=_fetch)
        choices = result.get("choices") or []
        if not choices:
            # A gateway/model misroute answers without a choices array: name the
            # shape instead of letting [0] raise IndexError (the boundary catch
            # below would report "list index out of range" and nothing else).
            return Attempt.impossible(f"{settings.llm_model} returned no choices")
        raw = (choices[0]["message"].get("content") or "").strip()
        if not raw:
            # A reasoning model that ignored the switch spends the budget
            # thinking and returns nothing — say so, don't crash on .strip().
            return Attempt.impossible(
                f"{settings.llm_model} returned no content "
                f"(finish_reason={choices[0].get('finish_reason')})"
            )
        description = raw.split(".")[0].strip() + "."
        _TownDescGateway._CACHE[key] = description
        return Attempt.succeeded(description)
    except (httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException):
        raise  # transient — let DAG retry handle it
    except (AttributeError, KeyError, OSError, TypeError, ValueError) as e:
        logger.warning("Failed to generate town description for %s", town_name, exc_info=True)
        return Attempt.impossible(f"town description generation failed: {e}")
