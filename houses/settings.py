"""Configuration — postcodes, API keys, sheet IDs."""

from typing import cast

from pint import Quantity
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_quantity(v: object, default_unit: str) -> Quantity:
    """Parse a value into a Quantity.

    Accepts:
    - ``Quantity`` — pass through
    - ``int`` / ``float`` — treated as magnitude in ``default_unit``
    - ``str`` like ``"10 km"`` — parsed by pint (number + optional unit)
    - ``dict`` with ``value`` and optional ``unit`` keys
    """
    # type: ignore[invalid-argument-type]  # pint's stubs don't expose Quantity as a runtime class; isinstance needs the real class
    if isinstance(v, cast(type, Quantity)):
        return v
    if isinstance(v, (int, float)):
        return Quantity(v, default_unit)
    if isinstance(v, str):
        # One honest parse: a settings value pint cannot read is an invalid
        # settings value — raise, never guess a fallback unit from float(v)
        # (which silently mangles "10 km" into "10 unit" failures and hides
        # the original value from every log).
        try:
            return Quantity(v)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Cannot convert settings value {v!r} to Quantity") from exc
    if isinstance(v, dict):
        return Quantity(v["value"], v.get("unit", default_unit))
    raise TypeError(f"Cannot convert {type(v).__name__} to Quantity")


class Settings(BaseSettings):
    host: str = "127.0.0.1"
    port: int = 8765
    reload: bool = True

    simon_destination: str = "1 Drummond Gate, Pimlico, London SW1V 2QQ"
    lorena_destination: str = "Eastgate House, 40 Dukes Place, Aldgate, London EC3A 7LP"
    bracknell_postcode: str = "RG12 8YA"

    tfl_api_key: str = Field(default="", alias="TFL_API_KEY")
    ors_api_key: str = Field(default="", alias="HEIGIT_API_KEY")
    # HeiGIT moved every API to api.heigit.org/<service>/<version>/ and
    # deprecated api.openrouteservice.org (2026-04-28); from 2026-08-27 the
    # deprecated host carries 10% of the quota and its usage is invisible in
    # the account dashboard. Paths live in houses/ors_endpoints.py.
    ors_base_url: str = Field(default="https://api.heigit.org", alias="HOUSES_ORS_BASE_URL")
    google_maps_api_key: str = Field(default="", alias="PLACES_API_KEY")
    # ALL LLM access goes through Cloudflare AI Gateway (skill://cloudflare-ai-gateway):
    # the gateway holds the provider keys (BYOK), picks the model via its text
    # route, and reports per-repo analytics. The credential is the GATEWAY token
    # (same secret PR-Agent uses) — the app never holds an OpenRouter key.
    llm_base_url: str = Field(
        default="https://gateway.ai.cloudflare.com/v1/e21a5be58ac1e8f7d5619539feb2dc3d/default/compat",
        alias="HOUSES_LLM_BASE_URL",
    )
    llm_api_key: str = Field(default="", alias="CLOUDFLARE_AIGATEWAY_TOKEN")
    # The model name selects Cloudflare's dynamic route, not a provider model:
    # `fallback2` is the text chain. The route's model reasons by default, so
    # town_desc sends reasoning={"enabled": false} — without it the whole token
    # budget goes to reasoning and the response carries content=None.
    llm_model: str = Field(default="dynamic/fallback2", alias="HOUSES_LLM_MODEL")
    llm_temperature: float = 0.7
    llm_max_tokens: int = 150
    trace: bool = Field(default=False, alias="HOUSES_TRACE")
    epc_bearer_token: str = Field(default="", alias="EPC_BEARER_TOKEN")
    web_client_id: str = Field(default="", alias="HOUSES_GOOGLE_WEB_CLIENT_ID")
    web_client_secret: str = Field(default="", alias="HOUSES_GOOGLE_WEB_CLIENT_SECRET")
    device_client_id: str = Field(default="", alias="HOUSES_GOOGLE_DEVICE_CLIENT_ID")
    device_client_secret: str = Field(default="", alias="HOUSES_GOOGLE_DEVICE_CLIENT_SECRET")
    session_secret: str = Field(default="", alias="HOUSES_SESSION_SECRET")

    rightmove_chrome_port: int = 9222
    rightmove_sample_page: str = ""
    rightmove_scraper_offline: bool = False

    petrol_mpg: float = 45.0
    petrol_price_per_litre: float = 1.45

    school_search_radius: Quantity = Quantity(5, "km")
    max_walk_to_station: Quantity = Quantity(20, "minute")
    bus_walk_penalty: Quantity = Quantity(10, "minute")

    _parse_school_radius = field_validator("school_search_radius", mode="before")(lambda v: _parse_quantity(v, "km"))
    _parse_max_walk = field_validator("max_walk_to_station", mode="before")(lambda v: _parse_quantity(v, "minute"))
    _parse_bus_penalty = field_validator("bus_walk_penalty", mode="before")(lambda v: _parse_quantity(v, "minute"))
    # Both base URLs are joined with a leading-slash path ("/chat/completions",
    # "/openrouteservice/v2/..."), so a configured trailing slash would produce
    # a double slash. Normalised HERE, where the value enters the app — every
    # caller then just concatenates.
    _strip_ors_url_slash = field_validator("ors_base_url")(lambda value: value.rstrip("/"))
    _strip_llm_url_slash = field_validator("llm_base_url")(lambda value: value.rstrip("/"))

    simon_station_crs: str = "VIC"
    lorena_station_crs: str = "FST"

    sqlite_path: str = Field(default="data/houses.db", alias="HOUSES_SQLITE_PATH")

    frontend_url: str = Field(default="http://localhost:5173", alias="HOUSES_FRONTEND_URL")
    public_url: str = Field(default="http://localhost:8765", alias="HOUSES_PUBLIC_URL")

    # The rollout's review surface. A browser signing in HERE must be
    # redirected back HERE — the box already carries its final production
    # public_url before the human approves it, so auth must follow the
    # hostname the user is actually on. Only hosts in this set (public_url,
    # review_url, frontend_url) are ever used for the OAuth redirect.
    review_url: str = Field(
        default="https://houses-smoke.blueumbrella.net", alias="HOUSES_REVIEW_URL"
    )

    working_weeks_per_year: int = 46
    weekly_simon_trips: int = 1
    weekly_lorena_trips: int = 2
    weekly_bracknell_trips: int = 1

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="HOUSES_",
        populate_by_name=True,
    )


settings = Settings()
