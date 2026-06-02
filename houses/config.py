"""Configuration — postcodes, API keys, sheet IDs."""

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    host: str = Field(default="127.0.0.1", env="HOUSES_HOST")
    port: int = Field(default=8080, env="HOUSES_PORT")
    reload: bool = Field(default=True, env="HOUSES_RELOAD")

    sheet_id: str = Field(default="", env="HOUSES_SHEET_ID")
    service_account_json: str = Field(default="", env="GOOGLE_SHEETS_SERVICE_ACCOUNT")

    simon_postcode: str = Field(default="SW1V 2QQ", env="HOUSES_SIMON_POSTCODE")
    lorena_postcode: str = Field(default="EC3A 7LP", env="HOUSES_LORENA_POSTCODE")
    bracknell_postcode: str = Field(default="RG12 8YA", env="HOUSES_BRACKNELL_POSTCODE")

    tfl_api_key: str = Field(default="", env="TFL_API_KEY")

    ors_api_key: str = Field(default="", env="HEIGIT_API_KEY")

    petrol_mpg: float = Field(default=45.0, env="HOUSES_PETROL_MPG")
    petrol_price_per_litre: float = Field(default=1.45, env="HOUSES_PETROL_PRICE")

    school_search_radius_km: float = Field(default=5.0, env="HOUSES_SCHOOL_RADIUS")

    model_config = {"env_file": ".env"}


settings = Settings()
