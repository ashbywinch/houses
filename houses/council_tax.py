"""Council tax band lookup via VOA website scraper + CivAccount rates."""

from __future__ import annotations

import csv
import logging
import re
from collections import namedtuple
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from money import Money

from dag.attempt import Attempt
from dag.measurement import Measurement
from houses.api_cache import cached_sync_client, get_cached, set_cached
from houses.council_tax_info import AnnexeDwelling, CouncilTaxInfo

logger = logging.getLogger(__name__)

BAND_RATIOS = {
    "A": 6 / 9,
    "B": 7 / 9,
    "C": 8 / 9,
    "D": 9 / 9,
    "E": 11 / 9,
    "F": 13 / 9,
    "G": 15 / 9,
    "H": 18 / 9,
}
CIVACCOUNT_URL = "https://www.civaccount.co.uk/api/v1/councils"
COUNCIL_TAX_CSV = "data/council_tax_rates.csv"

# In-memory cache: lowercased authority name -> Band D rate (float)
_cached_rates: dict[str, float] | None = None


def _reset():
    """Clear the cached council tax rates for test isolation."""
    global _cached_rates
    _cached_rates = None


# Legacy district -> current billing authority.  VOA rows are keyed by the
# district that existed when the row was created; the government Band D CSV
# uses today's authorities.  Buckinghamshire's 2020 unitary reorg merged the
# four districts below into one UA.
_RATE_ALIASES = {
    "wycombe": "buckinghamshire ua",
    "aylesbury vale": "buckinghamshire ua",
    "chiltern": "buckinghamshire ua",
    "south bucks": "buckinghamshire ua",
}


def _load_rates() -> dict[str, float]:
    global _cached_rates
    if _cached_rates is not None:
        return _cached_rates
    _cached_rates = {}
    path = Path(__file__).parent.parent / COUNCIL_TAX_CSV
    if not path.is_file():
        logger.warning("Council tax rates CSV not found at %s", path)
        return _cached_rates
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rate = row.get("band_d_rate", "")
            if rate:
                _cached_rates[row["authority"].strip().lower()] = float(rate)
    logger.debug("Loaded %d council tax rates from %s", len(_cached_rates), COUNCIL_TAX_CSV)
    return _cached_rates


def _extract_building(address: str) -> dict:
    """Extract building name/number and postcode from an address."""
    parts = [p.strip() for p in address.split(",")]
    first = parts[0] if parts else ""
    last = parts[-1] if parts else ""
    # Check if last part looks like a postcode
    pc_match = re.search(r"[A-Z]{1,2}[0-9][A-Z0-9]? ?[0-9][A-Z]{2}", last, re.IGNORECASE)
    postcode = pc_match.group(0) if pc_match else ""
    if not postcode:
        outcode_match = re.search(r"^[A-Z]{1,2}[0-9][A-Z0-9]?$", last, re.IGNORECASE)
        postcode = last if outcode_match else ""

    building = first
    num_match = re.match(r"^(\d+[A-Z]?)\s", building)
    if num_match:
        return {"postcode": postcode, "building_number": num_match.group(1)}
    # A unit descriptor ("Flat 3") names a unit INSIDE a building — the
    # building descriptor is the next part of the address.
    unit_match = re.match(r"^(flat|unit|apartment|maisonette)\s+\d+[A-Z]?$", building, re.IGNORECASE)
    if unit_match and len(parts) > 1:
        return {"postcode": postcode, "unit": building, "building_name": parts[1]}
    return {"postcode": postcode, "building_name": building}


def _normalise(text: str) -> str:
    """Strip whitespace, uppercase, remove punctuation for comparison."""
    return re.sub(r"[^A-Z0-9 ]", "", text.upper().strip())


def _normalise_keep_commas(text: str) -> str:
    """Uppercase, collapse whitespace, drop punctuation EXCEPT commas —
    the comma is the boundary marker between a building name and the
    rest of a VOA address ("THE OLD RECTORY, HIGH WYCOMBE")."""
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9,]", " ", text.upper())).strip()


_POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s+(\d[A-Z]{2})\b")


def _strip_postcode(tokens: list[str], address: str) -> list[str]:
    """Drop the postcode tokens from a normalized token list.

    VOA rows end with the postcode while the query may carry a county
    between locality and postcode ("2 WILLOWMEAD GARDENS, MARLOW,
    BUCKINGHAMSHIRE, SL7 1HW").  The county token must not break the
    token-aligned prefix/suffix comparisons below.
    """
    m = _POSTCODE_RE.search(_normalise(address))
    if not m:
        return tokens
    drop = set(m.groups())
    return [t for t in tokens if t not in drop]


def _lookup_yearly_cost(band: str, local_authority: str) -> Money | None:
    """Fetch the Band D rate from CivAccount, falling back to the CSV.

    Returns a ``Money`` in GBP, or ``None`` if no rate could be found.

    CivAccount is called via ``cached_sync_client`` which automatically
    caches every response to disk — no manual ``get_cached``/``set_cached``
    needed.
    """
    slug = local_authority.lower().replace(" ", "-").replace(".", "")
    url = f"{CIVACCOUNT_URL}/{slug}"
    try:
        with cached_sync_client(timeout=10.0) as client:
            civ = client.get(url)
            if civ.status_code == 200:
                civ_data = civ.json()
                band_d_rate = civ_data.get("band_d_rate")
                if band_d_rate and band in BAND_RATIOS:
                    return Money(str(round(band_d_rate * BAND_RATIOS[band], 2)), "GBP")
    except Exception:
        logger.warning("CivAccount lookup failed for %s (%s)", local_authority, slug)

    # 2) Fall back to the cached CSV of government Band D rates
    rates = _load_rates()
    norm = local_authority.strip().lower()
    # Try exact match first, then prefix match (e.g. "Woking" matches "Woking")
    band_d_rate = rates.get(norm)
    if band_d_rate is None:
        for key, val in rates.items():
            if key.startswith(norm) or norm.startswith(key):
                band_d_rate = val
                break

    # 2b) VOA rows carry LEGACY district names (e.g. "Wycombe"), but the
    #     CSV is keyed by today's billing authorities (the 2020 unitary
    #     reorg merged the Buckinghamshire districts into one UA).
    if band_d_rate is None and norm in _RATE_ALIASES:
        norm = _RATE_ALIASES[norm]
        band_d_rate = rates.get(norm)

    # 3) London boroughs: the CSV only has an aggregate "London boroughs" entry.
    #    Individual borough names (Ealing, Westminster, etc.) don't appear.
    if band_d_rate is None:
        _london_boroughs = frozenset(
            {
                "barking and dagenham",
                "barnet",
                "bexley",
                "brent",
                "bromley",
                "camden",
                "croydon",
                "ealing",
                "enfield",
                "greenwich",
                "hackney",
                "hammersmith and fulham",
                "haringey",
                "harrow",
                "havering",
                "hillingdon",
                "hounslow",
                "islington",
                "kensington and chelsea",
                "kingston upon thames",
                "lambeth",
                "lewisham",
                "merton",
                "newham",
                "redbridge",
                "richmond upon thames",
                "southwark",
                "sutton",
                "tower hamlets",
                "waltham forest",
                "wandsworth",
                "westminster",
                "city of london",
                "city of westminster",
            }
        )
        if norm in _london_boroughs or norm.replace(" ", "") in _london_boroughs:
            for key, val in rates.items():
                if "london borough" in key.lower():
                    band_d_rate = val
                    break

    if band_d_rate and band in BAND_RATIOS:
        return Money(str(round(band_d_rate * BAND_RATIOS[band], 2)), "GBP")

    return None


class CachedVOAClient:
    """Async context manager that wraps ``VOAClient`` with disk caching.

    ``VOAClient`` has its own internal HTTP client that bypasses our
    ``CachingTransport``, so caching must be added at the ``fetch_page``
    level.  This wrapper provides the same async context manager interface
    as ``VOAClient`` and automatically serializes/restores page results
    to/from the ``data/api_cache/`` disk cache.
    """

    _VoaRow = namedtuple("_VoaRow", ["band", "address", "postcode", "local_authority"])

    def __init__(self):
        self._inner: object | None = None

    async def __aenter__(self):
        from uk_property_apis.voa import VOAClient

        self._inner = VOAClient()
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *args):
        if self._inner is not None:
            await self._inner.__aexit__(*args)

    async def fetch_page(self, postcode: str, page: int = 0):
        key = f"voa/{postcode.strip().upper()}"
        cached = get_cached("GET", key)
        if cached is not None:
            rows = []
            for r in cached.get("rows", []):
                r.setdefault("postcode", "")
                r.setdefault("local_authority", "")
                rows.append(self._VoaRow(**r))
            return type("Page", (), {"rows": rows})()

        result = await self._inner.fetch_page(postcode, page=page)
        rows = [
            {"band": r.band, "address": r.address, "postcode": r.postcode, "local_authority": r.local_authority}
            for r in result.rows
        ]
        set_cached("GET", key, None, None, {"rows": rows})
        return result


async def lookup_council_tax(
    postcode: str,
    address: str = "",
    *,
    page_fetcher: Callable[[str, int], Any] | None = None,
) -> Attempt[CouncilTaxInfo]:
    """Look up council tax band via VOA website scraper.

    Returns an ``Attempt[CouncilTaxInfo]``. When the address is ambiguous
    (matches multiple properties) or no identifier can be extracted, the
    attempt carries the reason (e.g. ``"address matched multiple properties"``).

    Server callers extract the value with ``.value_or_none()`` and store
    ``""`` on the EnrichedProperty for failures.  ``page_fetcher`` is a
    test seam (callable(postcode, page) -> object with ``.rows``) so
    tests never mock uk_property_apis.
    """
    try:
        if page_fetcher is not None:
            page = page_fetcher(postcode, 0)
            if hasattr(page, "__await__"):
                page = await page
        else:
            async with CachedVOAClient() as client:
                page = await client.fetch_page(postcode)
        results_raw = [{"address": r.address, "band": r.band, "local_authority": r.local_authority} for r in page.rows]
    except ImportError:
        logger.warning("uk-property-apis not installed; skipping council tax lookup")
        return Attempt.impossible("uk-property-apis not installed")
    except (httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException):
        raise  # transient — let DAG retry handle it
    except Exception as e:
        logger.warning("VOA council tax lookup failed for %s: %s", postcode, e)
        return Attempt.impossible(f"VOA lookup failed: {e}")

    if not address:
        logger.debug("No address provided — cannot positively identify property")
        return Attempt.impossible("no address provided")

    active = [r for r in results_raw if r["band"] in BAND_RATIOS or r["band"] == "I"]
    if not active:
        logger.debug("VOA returned no active properties for %s", postcode)
        return Attempt.impossible("no active properties in VOA results")

    building = _extract_building(address)
    building_id = building.get("building_number") or building.get("building_name") or ""
    norm_id = _normalise(building_id)

    if not norm_id:
        logger.debug("Could not extract building identifier from address %r", address)
        return Attempt.impossible("could not extract building identifier")

    if building.get("building_number"):
        # A house number identifies the property only when it is followed
        # by the street name — "10" matches "FLAT 2ND FLR 10 DOWNING
        # STREET" (that flat IS at 10 Downing Street) but "5" must not
        # match "FLAT 5, 15 HIGH STREET" (that flat is at 15), nor
        # "15 HIGH STREET" (whole-token), nor "110 DOWNING STREET" nor
        # "A5 HIGH STREET" (letter- or digit-prefixed substrings).
        addr_tokens = _normalise(address).split()
        try:
            house_idx = addr_tokens.index(norm_id)
        except ValueError:
            house_idx = -1
        if house_idx < 0 or house_idx + 1 >= len(addr_tokens):
            # No street token follows the number — pairing it would be
            # guessing; fail closed.
            return Attempt.impossible("address does not identify a single property")
        street_first = addr_tokens[house_idx + 1]
        pattern = rf"(?<![A-Z0-9]){re.escape(norm_id)}\s+{re.escape(street_first)}(?![A-Z0-9])"
        matches = [r for r in active if re.search(pattern, _normalise(r["address"]))]
    else:
        # A NAME identifier only identifies the property when it is the
        # building descriptor at the START of the VOA address AND is
        # followed by a comma or the end of the address — "The Old
        # Rectory" must not claim "The Old Rectory Cottage". The comma
        # survives in _normalise_keep_commas (plain _normalise strips
        # punctuation). Substring matching is never used: a street-level
        # address must not claim a numbered property ("Rupert Avenue"
        # matched the row "1 RUPERT AVENUE" and took its band).
        name_norm = _normalise_keep_commas(building_id)
        unit_norm = _normalise_keep_commas(building.get("unit") or "")
        unit_prefixes = ("FLAT", "UNIT", "APARTMENT", "MAISONETTE")
        matches = []
        for r in active:
            addr = _normalise_keep_commas(r["address"])
            if unit_norm and addr.startswith(unit_norm + ", " + name_norm):
                # VOA rows for a flat at a numbered building put the unit
                # first — "FLAT 3, 123 HIGH STREET" — the query's unit
                # plus building identifies the row.
                matches.append(r)
                continue
            if not addr.startswith(name_norm):
                continue
            tail = addr[len(name_norm):].lstrip()
            if tail == "":
                matches.append(r)
            elif tail.startswith(","):
                tokens = tail[1:].lstrip().split()
                if not tokens:
                    matches.append(r)
                    continue
                first = tokens[0].rstrip(",")
                if unit_norm:
                    # The query names a specific unit ("Flat 3, The Old
                    # Rectory") — accept rows carrying that unit.
                    rest_upper = " ".join(t.strip(",") for t in tokens).upper()
                    if rest_upper == unit_norm or rest_upper.startswith(unit_norm + " "):
                        matches.append(r)
                else:
                    is_unit = first.upper() in unit_prefixes or (len(tokens) == 1 and first.isdigit())
                    if not is_unit:
                        matches.append(r)
        if not matches:
            # The name is present but never identifies a unit on its own.
            near = [r for r in active if norm_id in _normalise(r["address"])]
            if near:
                logger.debug("Address %r names a building/street but no specific unit in %s", building_id, postcode)
                return Attempt.impossible("address does not identify a single property")

    if not matches:
        logger.debug("Could not match building %r in VOA results for %s", building_id, postcode)
        return Attempt.impossible(f"no VOA match for building {building_id}")
    norm_query_tokens = _strip_postcode(_normalise(address).split(), address)
    exact_matches = []
    for m in matches:
        row_tokens = _strip_postcode(_normalise(m["address"]).split(), m["address"])
        if len(row_tokens) >= 2 and norm_query_tokens[: len(row_tokens)] == row_tokens:
            exact_matches.append(m)
    if len(exact_matches) >= 1:
        # Multiple exact prefixes are the SAME property with locality
        # variants ("2 WILLOWMEAD GARDENS" vs "… MARLOW") — collapse when
        # the band agrees; different bands on the same designation is a
        # real conflict that falls through to the ambiguity error.
        bands = {m["band"] for m in exact_matches}
        matches = [exact_matches[0]] if len(bands) == 1 else exact_matches

    # Ambiguity check: more than one distinct address matches.  The error
    # names the first two (sorted, deterministic) and the total count so
    # the provenance is actually troubleshooting-useful — "matched
    # multiple properties" alone says nothing about WHAT was ambiguous.
    unique_addresses = sorted({m["address"] for m in matches})
    if len(unique_addresses) > 1:
        logger.debug(
            "Ambiguous address %r — matched %d different VOA addresses for %s",
            building_id,
            len(unique_addresses),
            postcode,
        )
        sample = ", ".join(repr(a) for a in unique_addresses[:2])
        return Attempt.impossible(
            f"address matched multiple properties: {sample} ({len(unique_addresses)} matches)"
        )

    matched = matches[0]

    # Annexe detection: the exact match IS the property.  An annexe is a
    # single OTHER VOA property whose address is the main address with a
    # unit prefix (a superset) — "FLAT 2, 2 WILLOWMEAD GARDENS" contains
    # "2 WILLOWMEAD GARDENS".  Locality-suffixed duplicates ("2
    # WILLOWMEAD GARDENS MARLOW") are the same property, not an annexe.
    annexe = None
    main_tokens = _strip_postcode(_normalise(matched["address"]).split(), matched["address"])
    annexe_rows = []
    for r in active:
        row_tokens = _strip_postcode(_normalise(r["address"]).split(), r["address"])
        if row_tokens == main_tokens:
            continue
        # The main designation appears contiguously after a unit prefix —
        # at index >= 1 so a locality-suffixed duplicate (index 0) is not
        # an annexe.  Trailing locality/county after the designation is
        # tolerated ("FLAT 2, 2 WILLOWMEAD GARDENS, MARLOW, BUCKINGHAMSHIRE").
        if len(row_tokens) > len(main_tokens):
            for i in range(1, len(row_tokens) - len(main_tokens) + 1):
                if row_tokens[i : i + len(main_tokens)] == main_tokens:
                    annexe_rows.append(r)
                    break
    if len(annexe_rows) == 1:
        r = annexe_rows[0]
        annexe_yearly = (
            _lookup_yearly_cost(r["band"], r["local_authority"]) if r["local_authority"] else None
        )
        annexe = AnnexeDwelling(
            address=r["address"],
            band=r["band"],
            yearly_cost=Measurement(annexe_yearly, 0.0) if annexe_yearly is not None else None,
        )

    yearly_cost = None
    evidence_url = ""
    lookup_error = ""
    if matched["local_authority"]:
        slug = matched["local_authority"].lower().replace(" ", "-").replace(".", "")
        evidence_url = f"https://www.civaccount.co.uk/councils/{slug}"
        yearly_cost = _lookup_yearly_cost(matched["band"], matched["local_authority"])
        if yearly_cost is None:
            # The band is real but the rate is not — the provenance must
            # say why there is no figure instead of silently omitting it.
            lookup_error = f"no yearly rate found for {matched['local_authority']}"
    else:
        logger.warning("No local authority found for %s postcode %s", building_id, postcode)

    return Attempt.succeeded(
        CouncilTaxInfo(
            band=matched["band"],
            yearly_cost=Measurement(yearly_cost, 0.0) if yearly_cost is not None else None,
            evidence_url=evidence_url,
            lookup_error=lookup_error,
            annexe=annexe,
        ),
    )
