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

    # Cap upstream response size when output scanning is enabled. Buffer-and-scan
    # would otherwise be unbounded — a malicious or buggy upstream can OOM the
    # proxy. Default 4 MiB (large enough for the longest reasonable completion).
    max_response_bytes: int = Field(default=4_194_304, ge=1024)

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

    # --- Output scanning (v0.4) ---------------------------------------------
    # Scans the LLM's *response* (not the user's prompt) for PII, API keys,
    # private keys, etc. Disabled by default so the v0.3.x behavior is
    # preserved. When enabled, the streaming endpoint will buffer the full
    # response before scanning — true incremental scanning lands in v0.5.
    output_scan_enabled: bool = False
    output_scan_mode: Literal["block", "redact", "log"] = "redact"
    output_scan_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    output_scan_marker: str = "[REDACTED:{rule_id}]"

    # --- Embedding similarity layer (v0.5) -----------------------------------
    # Third defense layer: catches prompts semantically similar to known
    # jailbreaks even when no regex rule fires. Off by default — requires
    # the optional [embeddings] extra (`pip install mithril-llm[embeddings]`)
    # which pulls sentence-transformers (~500 MB with torch).
    embedding_enabled: bool = False
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    embedding_corpus_path: str = ""  # empty = use bundled corpus

    # How streaming responses are scanned when output_scan_enabled is true.
    #   incremental — forward chunks as they arrive; scan after each. Block
    #                 mode emits a final error event and closes. Log mode
    #                 records findings without altering the stream. (Redact
    #                 mode falls back to `buffer` because true streaming
    #                 redaction needs a trail-buffer algorithm — v0.6.)
    #   buffer      — collect the whole SSE stream, scan as a whole, re-emit.
    #                 The v0.4 default. Breaks streaming UX but supports
    #                 redaction.
    output_scan_stream_mode: Literal["incremental", "buffer"] = "incremental"

    @model_validator(mode="after")
    def _validate_output_scan_marker(self) -> "Settings":
        """The marker is a Python format string. Fail fast at startup if it has
        a malformed placeholder, so a misconfiguration doesn't crash a live
        proxy on the first response that needs redaction."""
        try:
            self.output_scan_marker.format(
                rule_id="TEST", detector="test", severity="info"
            )
        except (IndexError, KeyError, ValueError) as exc:
            raise ValueError(
                f"MITHRIL_OUTPUT_SCAN_MARKER is not a valid format string "
                f"({type(exc).__name__}: {exc}). Allowed placeholders: "
                f"{{rule_id}}, {{detector}}, {{severity}}."
            ) from exc
        return self

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
