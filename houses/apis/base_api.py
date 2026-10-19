"""Shared transport access for the API classes: one guarded call + the DI seam."""

from __future__ import annotations

from typing import Any

from houses import apigw
from houses.apis.fetch_args import FetchArgs


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