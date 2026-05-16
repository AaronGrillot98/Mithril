from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MITHRIL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Proxy ---------------------------------------------------------------
    upstream_url: str = "https://api.openai.com/v1"
    host: str = "0.0.0.0"
    port: int = 8080
    mode: Literal["block", "log"] = "block"
    threshold: float = 0.7
    db_path: str = "mithril.db"

    # --- LLM judge (v0.2) ----------------------------------------------------
    # The judge is an optional second-opinion model that runs on requests
    # falling into the "ambiguous zone" — neither clearly safe nor clearly
    # malicious by regex alone. Disabled by default so v0.1 behavior is
    # preserved.
    judge_enabled: bool = False

    # Provider for the judge. "openai_compat" works with OpenAI, Together,
    # Groq, Ollama, vLLM, LM Studio, llama.cpp — anything speaking the
    # OpenAI chat-completions schema. "none" disables the judge.
    judge_provider: Literal["openai_compat", "none"] = "openai_compat"
    judge_base_url: str = "https://api.openai.com/v1"
    judge_model: str = "gpt-4o-mini"
    judge_api_key: str = ""

    # Ambiguous zone: score must be strictly between these two for the judge
    # to be invoked. Outside this band, the regex verdict stands.
    judge_low_threshold: float = 0.2
    judge_high_threshold: float = 0.9

    # Behavior on judge errors. "open" = trust regex verdict if judge fails
    # (favors availability). "closed" = block if judge fails (favors safety).
    judge_fail_mode: Literal["open", "closed"] = "open"

    judge_timeout: float = 5.0


settings = Settings()
