from __future__ import annotations

from mithril.config import Settings
from mithril.judges.base import Judge
from mithril.judges.noop import NoopJudge
from mithril.judges.openai_compat import OpenAICompatibleJudge


def build_judge(settings: Settings) -> Judge:
    """Construct the configured Judge instance.

    Returns a NoopJudge when the judge is disabled or the provider is "none".
    Callers can always invoke `.verdict()` without checking for None.
    """
    if not settings.judge_enabled or settings.judge_provider == "none":
        return NoopJudge()

    if settings.judge_provider == "openai_compat":
        return OpenAICompatibleJudge(
            base_url=settings.judge_base_url,
            model=settings.judge_model,
            api_key=settings.judge_api_key,
            timeout=settings.judge_timeout,
        )

    raise ValueError(f"Unknown judge provider: {settings.judge_provider!r}")
