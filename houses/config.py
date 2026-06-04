"""Configuration — postcodes, API keys, sheet IDs."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    host: str = "127.0.0.1"
    port: int = 8080
    reload: bool = True

    sheet_id: str = "1CUWQfy5KnyKko2L-H7whQbOVYL_Uzr_5JSHO6HpH_2s"

    simon_postcode: str = "SW1V 2QQ"
    lorena_postcode: str = "EC3A 7LP"
    bracknell_postcode: str = "RG12 8YA"

    tfl_api_key: str = Field(default="", alias="TFL_API_KEY")
    ors_api_key: str = Field(default="", alias="HEIGIT_API_KEY")
    service_account_json: str = Field(default="", alias="GOOGLE_SHEETS_SERVICE_ACCOUNT")
    google_maps_api_key: str = Field(default="", alias="PLACES_API_KEY")
    llm_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    llm_model: str = Field(default="deepseek/deepseek-chat", alias="HOUSES_LLM_MODEL")
    llm_temperature: float = 0.7
    llm_max_tokens: int = 150
    epc_bearer_token: str = Field(default="", alias="EPC_BEARER_TOKEN")

    test_sheet_id: str = "1I0LSMRRA2hzLdS1Jfjht8Av908a4Ttf99Ly0p0lnseA"

    petrol_mpg: float = 45.0
    petrol_price_per_litre: float = 1.45

    school_search_radius_km: float = 5.0

    simon_station_crs: str = "VIC"
    lorena_station_crs: str = "FST"

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
