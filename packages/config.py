from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str
    redis_url: str
    gemini_api_key: str = ""
    shopify_base_url: str = "http://localhost:9000/shopify"
    meta_base_url: str = "http://localhost:9000/meta"
    shiprocket_base_url: str = "http://localhost:9000/shiprocket"


settings = Settings()  # type: ignore[call-arg]

