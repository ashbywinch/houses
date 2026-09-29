"""postcodes.io — UK postcode / outcode lookup."""

from __future__ import annotations

import re

from houses import apigw
from houses.apis.transport import BaseApi, FetchArgs
from houses.geopoint import GeoPoint

# An outcode is the postcode's first 1-2 letters, one digit and an optional
# trailing letter (SW1, SW1A). "Not all digits" is wrong — every real
# postcode has digits — so the discriminator is this regex, exactly the
# postcodes.io contract the previous implementation used.
OUTCODE_RE = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]?$")


class PostcodesApi(BaseApi):
    profile = apigw.POSTCODESIO
    url = "https://api.postcodes.io/postcodes"
    outcode_url = "https://api.postcodes.io/outcodes"

    async def geocode(self, postcode: str, *, _client_factory=None, _no_cache: bool = False) -> GeoPoint | None:
        """Geocode a postcode or outcode via postcodes.io; None = not found."""
        key = postcode.strip().upper()
        if not key:
            return None
        url = f"{self.outcode_url}/{key}" if OUTCODE_RE.match(key) else f"{self.url}/{key}"
        req = FetchArgs("GET", url, _client_factory=_client_factory, no_cache=_no_cache)
        data = await self._fetch(req)
        if not data:
            return None
        result = data.get("result")
        if not result:
            return None
        return GeoPoint(result["latitude"], result["longitude"])
