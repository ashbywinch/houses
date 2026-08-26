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
        try:
            return Quantity(v)
        # lucidlint: ignore broad-except non-numeric settings value falls back to the Quantity default unit parse
        except Exception:
            return Quantity(float(v), default_unit)
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
    google_maps_api_key: str = Field(default="", alias="PLACES_API_KEY")
    llm_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    llm_model: str = Field(default="deepseek/deepseek-chat", alias="HOUSES_LLM_MODEL")
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

    simon_station_crs: str = "VIC"
    lorena_station_crs: str = "FST"

    sqlite_path: str = Field(default="data/houses.db", alias="HOUSES_SQLITE_PATH")

    frontend_url: str = Field(default="http://localhost:5173", alias="HOUSES_FRONTEND_URL")
    public_url: str = Field(default="http://localhost:8765", alias="HOUSES_PUBLIC_URL")

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

