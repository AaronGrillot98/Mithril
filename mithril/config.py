from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MITHRIL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    upstream_url: str = "https://api.openai.com/v1"
    host: str = "0.0.0.0"
    port: int = 8080
    mode: Literal["block", "log"] = "block"
    threshold: float = 0.7
    db_path: str = "mithril.db"


settings = Settings()
