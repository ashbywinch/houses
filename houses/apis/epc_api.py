"""UK gov EPC register search."""

from __future__ import annotations

from houses import apigw
from houses.apis.base_api import BaseApi
from houses.apis.epc_search_result import EpcSearchResult
from houses.apis.fetch_args import FetchArgs
from houses.settings import settings
from serialization import SerializableAsDict


class EpcApi(BaseApi):
    profile = apigw.GOV_EPC

    @staticmethod
    def _auth_headers() -> dict[str, str]:
        return {"Authorization": f"Bearer {settings.epc_bearer_token}"}

    async def search(self, url: str, params: SerializableAsDict, *, _client_factory=None) -> EpcSearchResult:
        """Register search; the caller matches its building among the rows."""
        req = FetchArgs(
            "GET",
            url,
            params=params,
            headers={"Accept": "application/json", **self._auth_headers()},
            _client_factory=_client_factory,
        )
        data = await self._fetch(req)
        if not isinstance(data, dict):
            return EpcSearchResult(certificates=[])
        return EpcSearchResult(certificates=data.get("data", []))
