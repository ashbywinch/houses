"""Council tax band lookup via VOA website scraper + CivAccount rates."""

from __future__ import annotations

import csv
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from money import Money
from uk_property_apis.voa import VOAClient

from dag.attempt import Attempt
from dag.measurement import Measurement
from houses.address_utils import normalise as _normalise
from houses.address_utils import strip_postcode as _strip_postcode
from houses.api_cache import cached_sync_client, get_cached, set_cached
from houses.council_tax_info import AnnexeDwelling, CouncilTaxInfo

if TYPE_CHECKING:
    from uk_property_apis.voa import VOAClient

logger = logging.getLogger(__name__)

# lucidlint: ignore global-state statutory Band-D ratio table; never mutated (data, not state)
# lucidlint: ignore record-shape statutory ratio table — data, not a record (review-log)
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
HTTP_OK = 200


def _reset():
    """Clear the cached council tax rates for test isolation."""
    # lucidlint: ignore global-state deliberate test seam — isolation_fixtures calls _reset() to clear cached rates
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
    # lucidlint: ignore global-state lazy memo of the rates CSV; single writer _load_rates
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


@dataclass(frozen=True)
class _BuildingMatch:
    """The building descriptor parsed from an address (keys present only
    when identified)."""

    postcode: str
    building_number: str | None = None
    unit: str | None = None
    building_name: str | None = None

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        d = dict(postcode=self.postcode)
        if self.building_number is not None:
            d["building_number"] = self.building_number
        if self.unit is not None:
            d["unit"] = self.unit
        if self.building_name is not None:
            d["building_name"] = self.building_name
        return d


def _extract_building(address: str) -> _BuildingMatch:
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
        return _BuildingMatch(postcode=postcode, building_number=num_match.group(1))
    # A unit descriptor ("Flat 3") names a unit INSIDE a building — the
    # building descriptor is the next part of the address.
    unit_match = re.match(r"^(flat|unit|apartment|maisonette)\s+\d+[A-Z]?$", building, re.IGNORECASE)
    if unit_match and len(parts) > 1:
        return _BuildingMatch(postcode=postcode, unit=building, building_name=parts[1])
    return _BuildingMatch(postcode=postcode, building_name=building)


def _normalise_keep_commas(text: str) -> str:
    """Uppercase, collapse whitespace, drop punctuation EXCEPT commas —
    the comma is the boundary marker between a building name and the
    rest of a VOA address ("THE OLD RECTORY, HIGH WYCOMBE")."""
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9,]", " ", text.upper())).strip()


def _civaccount_rate(
    band: str,
    local_authority: str,
    slug: str,
    client_factory: Callable[..., Any],
) -> Money | None:
    """Fetch the Band D rate from CivAccount, or ``None`` when unavailable."""
    url = f"{CIVACCOUNT_URL}/{slug}"
    try:
        with client_factory(timeout=10.0) as client:
            civ = client.get(url)
            if civ.status_code == HTTP_OK:
                civ_data = civ.json()
                band_d_rate = civ_data.get("band_d_rate")
                if band_d_rate and band in BAND_RATIOS:
                    return Money(str(round(band_d_rate * BAND_RATIOS[band], 2)), "GBP")
    # lucidlint: ignore broad-except API fallback — any CivAccount lookup failure degrades to None (unavailable)
    except Exception:
        logger.warning("CivAccount lookup failed for %s (%s)", local_authority, slug)
        return None
    return None


def _index_token(tokens: list[str], token: str) -> int:
    """Index of *token* in *tokens*, or -1 when absent (sentinel)."""
    try:
        return tokens.index(token)
    except ValueError:
        return -1


def _csv_band_d_rate(
    load_rates_fn: Callable[[], dict[str, float]],
    local_authority: str,
) -> float | None:
    """Band D rate from the cached government CSV, or ``None``.

    Exact match first, then prefix match, then the legacy-district
    aliases, then the aggregate "London boroughs" entry.
    """
    rates = load_rates_fn()
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

    return band_d_rate


def _lookup_yearly_cost(
    band: str,
    local_authority: str,
    *,
    load_rates_fn: Callable[[], dict[str, float]] | None = None,
    client_factory: Callable[..., Any] | None = None,
) -> Money | None:
    """Fetch the Band D rate from CivAccount, falling back to the CSV.

    Returns a ``Money`` in GBP, or ``None`` if no rate could be found.

    CivAccount is called via ``cached_sync_client`` which automatically
    caches every response to disk — no manual ``get_cached``/``set_cached``
    needed.

    ``load_rates_fn`` and ``client_factory`` are DI seams (tests inject
    fakes instead of patching ``_load_rates``/``httpx.Client``).
    """
    if client_factory is None:
        client_factory = cached_sync_client
    if load_rates_fn is None:
        load_rates_fn = _load_rates
    slug = local_authority.lower().replace(" ", "-").replace(".", "")
    civ_rate = _civaccount_rate(band, local_authority, slug, client_factory)
    if civ_rate is not None:
        return civ_rate

    # 2) Fall back to the cached CSV of government Band D rates
    band_d_rate = _csv_band_d_rate(load_rates_fn, local_authority)
    if band_d_rate and band in BAND_RATIOS:
        return Money(str(round(band_d_rate * BAND_RATIOS[band], 2)), "GBP")

    return None


@dataclass(frozen=True)
class _VoaRow:
    """One VOA valuation row as cached/serialized (band, address, council).

    ``postcode`` defaults to "" because stubbed rows in tests and some
    legacy cached entries carry no postcode."""

    band: str
    address: str
    postcode: str = ""
    local_authority: str | None = None

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction mirrors the cached wire shape (coding-standards.md)
        return dict(
            band=self.band, address=self.address, postcode=self.postcode,
            local_authority=self.local_authority,
        )


class CachedVOAClient:
    """Async context manager that wraps ``VOAClient`` with disk caching.

    ``VOAClient`` has its own internal HTTP client that bypasses our
    ``CachingTransport``, so caching must be added at the ``fetch_page``
    level.  This wrapper provides the same async context manager interface
    as ``VOAClient`` and automatically serializes/restores page results
    to/from the ``data/api_cache/`` disk cache.
    """


    def __init__(self):
        self._inner: VOAClient | None = None

    async def __aenter__(self):
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
                rows.append(_VoaRow(**r))
            return type("Page", (), {"rows": rows})()

        if self._inner is None:
            raise RuntimeError("CachedVOAClient used before __aenter__")
        result = await self._inner.fetch_page(postcode, page=page)
        rows = [
            _VoaRow(
                band=r.band, address=r.address, postcode=r.postcode,
                local_authority=r.local_authority,
            ).to_dict()
            for r in result.rows
        ]
        set_cached("GET", key, None, None, {"rows": rows})
        return result
async def _fetch_voa_results(
    postcode: str,
    page_fetcher: Callable[[str, int], Any] | None,
    voa_client_factory: Callable[[], Any] | None,
):
    """Fetch the VOA rows for a postcode via the injected fetcher or the
    cached VOA client, normalized to plain dicts.

    ``page_fetcher`` may return a page or a coroutine for one; the async
    context managers are the real ``CachedVOAClient`` path.  httpx errors
    propagate for the caller's retry handling.
    """
    if page_fetcher is not None:
        page = page_fetcher(postcode, 0)
        if hasattr(page, "__await__"):
            page = await page
    elif voa_client_factory is not None:
        async with voa_client_factory() as client:
            page = await client.fetch_page(postcode)
    else:
        async with CachedVOAClient() as client:
            page = await client.fetch_page(postcode)
    return [
        _VoaRow(
            band=str(r.band), address=str(r.address),
            local_authority=str(r.local_authority or ""),
        ).to_dict()
        for r in page.rows
        if r.address and r.band
]

def _building_identifier(address: str):
    """The building descriptor dict, its id, and its normalized id."""
    building = _extract_building(address)
    building_id = building.building_number or building.building_name or ""
    return building, building_id, _normalise(building_id)


def _match_by_number(active, norm_id: str, address: str):
    """Rows whose address pairs the house number with the following street
    token; None when the address does not identify a single property.

    "10" matches "FLAT 2ND FLR 10 DOWNING STREET" (that flat IS at 10
    Downing Street) but "5" must not match "FLAT 5, 15 HIGH STREET"
    (that flat is at 15), nor "15 HIGH STREET" (whole-token), nor
    "110 DOWNING STREET" nor "A5 HIGH STREET" (letter- or digit-prefixed
    substrings).
    """
    addr_tokens = _normalise(address).split()
    house_idx = _index_token(addr_tokens, norm_id)
    if house_idx < 0 or house_idx + 1 >= len(addr_tokens):
        # No street token follows the number — pairing it would be
        # guessing; fail closed.
        return None
    street_first = addr_tokens[house_idx + 1]
    pattern = rf"(?<![A-Z0-9]){re.escape(norm_id)}\s+{re.escape(street_first)}(?![A-Z0-9])"
    return [r for r in active if re.search(pattern, _normalise(r["address"]))]


def _is_unit_descriptor(first: str, tokens: list[str]) -> bool:
    """True when the first address token names a unit ("FLAT", "APT 3")
    rather than the property itself."""
    return first.upper() in ("FLAT", "UNIT", "APARTMENT", "MAISONETTE") or (
        len(tokens) == 1 and first.isdigit()
    )


def _name_row_match(addr: str, name_norm: str, unit_norm: str) -> bool:
    """Whether one normalized VOA row address identifies the named building.

    The building descriptor must be at the START of the VOA address AND
    be followed by a comma or the end of the address — "The Old Rectory"
    must not claim "The Old Rectory Cottage".  Substring matching is never
    used: a street-level address must not claim a numbered property
    ("Rupert Avenue" must not match the row "1 RUPERT AVENUE").
    """
    if unit_norm and addr.startswith(unit_norm + ", " + name_norm):
        # VOA rows for a flat at a numbered building put the unit first —
        # "FLAT 3, 123 HIGH STREET" — the query's unit plus building
        # identifies the row.
        return True
    if not addr.startswith(name_norm):
        return False
    tail = addr[len(name_norm) :].lstrip()
    if tail == "":
        return True
    if not tail.startswith(","):
        return False
    tokens = tail[1:].lstrip().split()
    if not tokens:
        return True
    first = tokens[0].rstrip(",")
    if unit_norm:
        # The query names a specific unit ("Flat 3, The Old Rectory") —
        # accept rows carrying that unit.
        rest_upper = " ".join(t.strip(",") for t in tokens).upper()
        return rest_upper == unit_norm or rest_upper.startswith(unit_norm + " ")
    return not _is_unit_descriptor(first, tokens)


@dataclass(frozen=True)
class _PropertyRef:
    """The property a VOA match must positively identify.

    ``address`` is the query as given, ``building_id`` the identifier
    extracted from it, ``postcode`` the postcode both were queried with.
    """

    address: str
    building_id: str
    postcode: str


def _match_building_name(
    active,
    building,
    query: _PropertyRef,
):
    """Rows matching a named building; None when the name is present but
    never identifies a specific unit on its own (the caller reports the
    address as not identifying a single property)."""
    name_norm = _normalise_keep_commas(query.building_id)
    unit_norm = _normalise_keep_commas(building.unit or "")
    matches = [
        r
        for r in active
        if _name_row_match(_normalise_keep_commas(r["address"]), name_norm, unit_norm)
    ]
    if not matches:
        # The name is present but never identifies a unit on its own.
        norm_id = _normalise(query.building_id)
        if any(norm_id in _normalise(r["address"]) for r in active):
            logger.debug(
                "Address %r names a building/street but no specific unit in %s",
                query.building_id,
                query.postcode,
            )
            return None
    return matches


def _match_rows(active, building, query: _PropertyRef):
    """VOA rows matching the building identifier, or None when the address
    cannot positively identify a single property."""
    building_id = building.building_number or building.building_name or ""
    norm_id = _normalise(building_id)
    if building.building_number:
        return _match_by_number(active, norm_id, query.address)
    return _match_building_name(active, building, query)


def _collapse_exact_matches(matches, address: str):
    """Prefer rows whose address is a token-aligned prefix of the query.

    Multiple exact prefixes are the SAME property with locality variants
    ("2 WILLOWMEAD GARDENS" vs "… MARLOW") — collapse when the band
    agrees; different bands on the same designation is a real conflict
    that falls through to the ambiguity error.  Prefer the variant WITH a
    local_authority — the rate lookup must not fail because the first row
    happened to lack one (review).
    """
    norm_query_tokens = _strip_postcode(_normalise(address).split(), address)
    exact_matches = []
    for m in matches:
        row_tokens = _strip_postcode(_normalise(m["address"]).split(), m["address"])
        if len(row_tokens) >= 2 and norm_query_tokens[: len(row_tokens)] == row_tokens:
            exact_matches.append(m)
    if len(exact_matches) >= 1:
        bands = {m["band"] for m in exact_matches}
        if len(bands) == 1:
            return sorted(exact_matches, key=lambda m: not bool(m.get("local_authority")))[:1]
        return exact_matches
    return matches


def _select_matched_row(
    matches,
    query: _PropertyRef,
):
    """Reduce matches to one unambiguous row.

    Returns (row, None) when the address identifies a single property, or
    (None, reason) when it is ambiguous — the reason names the first two
    addresses (sorted, deterministic) and the total count so the
    provenance is actually troubleshooting-useful.
    """
    matches = _collapse_exact_matches(matches, query.address)
    unique_addresses = sorted({m["address"] for m in matches})
    if len(unique_addresses) > 1:
        logger.debug(
            "Ambiguous address %r — matched %d different VOA addresses for %s",
            query.building_id,
            len(unique_addresses),
            query.postcode,
        )
        sample = ", ".join(repr(a) for a in unique_addresses[:2])
        return None, f"address matched multiple properties: {sample} ({len(unique_addresses)} matches)"
    return matches[0], None


def _is_letter_suffix_annexe(row_tokens: list[str], main_tokens: list[str]) -> bool:
    """True when the row is the classic letter-suffixed annexe — the main
    number plus exactly one letter ("2A WILLOWMEAD GARDENS" vs
    "2 WILLOWMEAD GARDENS").  "12"/"20" digit-prefixed rows stay excluded."""
    return (
        len(row_tokens) >= len(main_tokens)
        and len(row_tokens[0]) == len(main_tokens[0]) + 1
        and row_tokens[0].startswith(main_tokens[0])
        and row_tokens[0][-1:].isalpha()
        and row_tokens[1 : 1 + len(main_tokens) - 1] == main_tokens[1:]
    )


def _find_annexe(
    active,
    matched,
    rate_lookup: Callable[[str, str], Money | None],
) -> AnnexeDwelling | None:
    """The single OTHER VOA property whose address is the main address
    with a unit prefix (a superset), or None.

    The exact match IS the property — "FLAT 2, 2 WILLOWMEAD GARDENS"
    contains "2 WILLOWMEAD GARDENS".  Locality-suffixed duplicates ("2
    WILLOWMEAD GARDENS MARLOW") are the same property, not an annexe.
    """
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
        if _is_letter_suffix_annexe(row_tokens, main_tokens):
            annexe_rows.append(r)
            continue
        if len(row_tokens) > len(main_tokens):
            for i in range(1, len(row_tokens) - len(main_tokens) + 1):
                if row_tokens[i : i + len(main_tokens)] == main_tokens:
                    annexe_rows.append(r)
                    break
    if len(annexe_rows) == 1:
        r = annexe_rows[0]
        annexe_yearly = rate_lookup(r["band"], r["local_authority"]) if r["local_authority"] else None
        return AnnexeDwelling(
            address=r["address"],
            band=r["band"],
            yearly_cost=Measurement(annexe_yearly, 0.0) if annexe_yearly is not None else None,
        )
    return None

def _lookup_matched_rate(
    matched,
    rate_lookup: Callable[[str, str], Money | None],
    query: _PropertyRef,
):
    """(yearly cost, evidence URL, lookup error) for the matched row.

    The CivAccount WEBSITE has no /councils/<slug> pages (they 404 for
    every authority) — the API endpoint that actually serves the rate is
    the only working evidence link.
    """
    yearly_cost = None
    evidence_url = ""
    lookup_error = ""
    if matched["local_authority"]:
        slug = matched["local_authority"].lower().replace(" ", "-").replace(".", "")
        evidence_url = f"{CIVACCOUNT_URL}/{slug}"
        yearly_cost = rate_lookup(matched["band"], matched["local_authority"])
        if yearly_cost is None:
            # The band is real but the rate is not — the provenance must
            # say why there is no figure instead of silently omitting it.
            lookup_error = f"no yearly rate found for {matched['local_authority']}"
    else:
        logger.warning("No local authority found for %s postcode %s", query.building_id, query.postcode)
    return yearly_cost, evidence_url, lookup_error


def _active_rows(results_raw):
    """Rows with a live band (A–H or I), excluding DELETED/archived rows."""
    return [r for r in results_raw if r["band"] in BAND_RATIOS or r["band"] == "I"]

def _match_failure(matches, query: _PropertyRef):
    """The impossible-reason when matches do not identify a single
    property; None to proceed with the match."""
    if matches is None:
        return "address does not identify a single property"
    if not matches:
        logger.debug(
            "Could not match building %r in VOA results for %s",
            query.building_id,
            query.postcode,
        )
        return f"no VOA match for building {query.building_id}"
    return None




# lucidlint: ignore latent-class state already explicit — after the _PropertyRef refactor only _find_annexe and
async def lookup_council_tax(
    postcode: str,
    address: str = "",
    *,
    page_fetcher: Callable[[str, int], Any] | None = None,
    rate_lookup: Callable[[str, str], Money | None] | None = None,
    voa_client_factory: Callable[[], Any] | None = None,
) -> Attempt[CouncilTaxInfo]:
    """Look up council tax band via VOA website scraper.

    Returns an ``Attempt[CouncilTaxInfo]``. When the address is ambiguous
    (matches multiple properties) or no identifier can be extracted, the
    attempt carries the reason (e.g. ``"address matched multiple properties"``).

    Server callers extract the value with ``.value_or_none()`` and store
    ``""`` on the EnrichedProperty for failures.  ``page_fetcher`` is a
    test seam (callable(postcode, page) -> object with ``.rows``) and
    ``voa_client_factory`` is another (callable() -> async context
    manager with ``fetch_page``) so tests never mock uk_property_apis.
    """
    if rate_lookup is None:
        rate_lookup = _lookup_yearly_cost
    try:
        results_raw = await _fetch_voa_results(postcode, page_fetcher, voa_client_factory)
    except (httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException):
        raise  # transient — let DAG retry handle it
    # lucidlint: ignore broad-except boundary — unknown VOA failures convert to an impossible attempt, never raise
    except Exception as e:
        logger.warning("VOA council tax lookup failed for %s: %s", postcode, e)
        return Attempt.impossible(f"VOA lookup failed: {e}")

    if not address:
        logger.debug("No address provided — cannot positively identify property")
        return Attempt.impossible("no address provided")

    active = _active_rows(results_raw)
    if not active:
        logger.debug("VOA returned no active properties for %s", postcode)
        return Attempt.impossible("no active properties in VOA results")

    building, building_id, norm_id = _building_identifier(address)
    # lucidlint: ignore duplicate-block intentional guard clauses — each VOA preflight (no active rows / no building
    if not norm_id:
        logger.debug("Could not extract building identifier from address %r", address)
        return Attempt.impossible("could not extract building identifier")

    query = _PropertyRef(address=address, building_id=building_id, postcode=postcode)
    matches = _match_rows(active, building, query)
    failure = _match_failure(matches, query)
    if failure is not None:
        return Attempt.impossible(failure)

    matched, match_error = _select_matched_row(matches, query)
    if match_error is not None or matched is None:
        return Attempt.impossible(match_error or "no matching VOA property row")

    annexe = _find_annexe(active, matched, rate_lookup)
    yearly_cost, evidence_url, lookup_error = _lookup_matched_rate(matched, rate_lookup, query)

    return Attempt.succeeded(
        CouncilTaxInfo(
            band=matched["band"],
            yearly_cost=Measurement(yearly_cost, 0.0) if yearly_cost is not None else None,
            evidence_url=evidence_url,
            lookup_error=lookup_error,
            annexe=annexe,
        ),
    )


