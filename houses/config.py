"""Configuration — postcodes, API keys, sheet IDs."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Server
    host: str = "127.0.0.1"
    port: int = 8080
    reload: bool = True

    # Google Sheets
    sheet_id: str = ""
    google_service_account_json: str = "service-account.json"

    # Commute anchors
    simon_postcode: str = "SW1V 2QQ"
    lorena_postcode: str = "EC3A 7LP"
    bracknell_postcode: str = "RG12 8YA"

    # Petrol calculation
    petrol_mpg: float = 45.0
    petrol_price_per_litre: float = 1.45

    # School search
    school_search_radius_km: float = 5.0

    # Transport API (optional — e.g. TfL, Google Maps)
    transit_api_key: str = ""

    model_config = {"env_prefix": "HOUSES_", "env_file": ".env"}


settings = Settings()
