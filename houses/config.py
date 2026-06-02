"""Configuration — postcodes, API keys, sheet IDs."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    host: str = "127.0.0.1"
    port: int = 8080
    reload: bool = True

    sheet_id: str = ""

    simon_postcode: str = "SW1V 2QQ"
    lorena_postcode: str = "EC3A 7LP"
    bracknell_postcode: str = "RG12 8YA"

    tfl_api_key: str = Field(default="", alias="TFL_API_KEY")
    ors_api_key: str = Field(default="", alias="HEIGIT_API_KEY")
    service_account_json: str = Field(default="", alias="GOOGLE_SHEETS_SERVICE_ACCOUNT")

    petrol_mpg: float = 45.0
    petrol_price_per_litre: float = 1.45

    school_search_radius_km: float = 5.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="HOUSES_",
        populate_by_name=True,
    )


settings = Settings()
