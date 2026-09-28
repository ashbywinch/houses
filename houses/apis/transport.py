"""Shared transport for the API classes: one guarded call + the DI seam.

Everything here is transport mechanics; each external API lives in its
own module (coding-standards: one class per module).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from houses import apigw


@dataclass(frozen=True)
class FetchArgs:
    """One guarded external call: what to send and where.

    A parameter object for the transport seam — every API method builds
    exactly one of these, then `_fetch` translates the transport errors.
    """

    method: str
    url: str
    params: Any = None
    body: Any = None
    headers: dict[str, str] | None = None
    wire_params: dict[str, str] | None = None
    _client_factory: Any = None
    no_cache: bool = False


class BaseApi:
    """Shared transport access: one error translation + the DI seam."""

    profile: apigw.ApiProfile

    async def _fetch(self, req: FetchArgs) -> Any:
        """The transport call; DailyQuotaError and HTTP are translated here.

        ``DailyQuotaError`` becomes ``None`` (a caller's keep-fallback
        signal). Transient (429/5xx) and auth/permanent HTTP errors still
        raise — the DAG classifier owns those decisions.
        """
        try:
            return await apigw.api_fetch(
                req.method,
                req.url,
                api=self.profile,
                params=req.params,
                body=req.body,
                headers=req.headers,
                wire_params=req.wire_params,
                _client_factory=req._client_factory,
                _no_cache=req.no_cache,
            )
        except apigw.DailyQuotaError:
            return None
