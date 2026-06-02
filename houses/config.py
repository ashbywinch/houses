"""Configuration — postcodes, API keys, sheet IDs."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
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

    # TravelTime API (for transit commute times)
    # Sign up at https://traveltime.com/free-api-trial
    traveltime_app_id: str = ""
    traveltime_api_key: str = ""

    # OpenRouteService API key (for driving distance — petrol calc)
    # Sign up at https://openrouteservice.org/dev/#/signup
    ors_api_key: str = ""

    # Petrol calculation
    petrol_mpg: float = 45.0
    petrol_price_per_litre: float = 1.45

    # School search
    school_search_radius_km: float = 5.0

    model_config = {"env_prefix": "HOUSES_", "env_file": ".env"}


settings = Settings()
