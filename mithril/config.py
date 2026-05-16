from typing import Literal

from pydantic import Field, model_validator
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
    port: int = Field(default=8080, ge=1, le=65535)
    mode: Literal["block", "log"] = "block"
    threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    db_path: str = "mithril.db"

    # Cap request body size to prevent DoS via giant prompts. Default 1 MiB.
    max_body_bytes: int = Field(default=1_048_576, ge=1024)

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
    judge_low_threshold: float = Field(default=0.2, ge=0.0, le=1.0)
    judge_high_threshold: float = Field(default=0.9, ge=0.0, le=1.0)

    # Behavior on judge errors. "open" = trust regex verdict if judge fails
    # (favors availability). "closed" = block if judge fails (favors safety).
    judge_fail_mode: Literal["open", "closed"] = "open"

    judge_timeout: float = Field(default=5.0, gt=0.0, le=300.0)

    @model_validator(mode="after")
    def _validate_judge_zone(self) -> "Settings":
        if self.judge_low_threshold >= self.judge_high_threshold:
            raise ValueError(
                f"MITHRIL_JUDGE_LOW_THRESHOLD ({self.judge_low_threshold}) must be "
                f"strictly less than MITHRIL_JUDGE_HIGH_THRESHOLD "
                f"({self.judge_high_threshold}); otherwise the ambiguous zone is empty."
            )
        return self


settings = Settings()
