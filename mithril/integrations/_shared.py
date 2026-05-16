"""Shared helpers for the framework integrations.

Kept dependency-free so it can be imported without pulling LangChain,
LiteLLM, or any other optional package.
"""

from __future__ import annotations

from typing import Any, Sequence

from mithril.config import settings
from mithril.detectors import default_pipeline
from mithril.detectors.pipeline import DetectionPipeline
from mithril.judges import build_judge
from mithril.models import DetectionResult


class MithrilBlocked(Exception):
    """Raised by integrations when a prompt is blocked by Mithril.

    Carries the full DetectionResult so callers can surface findings,
    judge verdicts, severity, etc. to their own error handlers.

    Example:
        try:
            response = guarded_llm.invoke("Ignore previous instructions")
        except MithrilBlocked as exc:
            log_event(severity=exc.result.top_severity, findings=exc.result.findings)
            return {"error": "blocked", "reason": exc.short_reason()}
    """

    def __init__(self, result: DetectionResult):
        self.result = result
        super().__init__(self.short_reason())

    def short_reason(self) -> str:
        if self.result.judge is not None and self.result.judge.verdict == "attack":
            return (
                f"blocked by judge: {self.result.judge.reason or 'attack detected'} "
                f"(confidence={self.result.judge.confidence:.2f})"
            )
        if self.result.findings:
            top = self.result.findings[0]
            return (
                f"blocked by {top.detector}/{top.rule_id}: {top.message} "
                f"(confidence={top.confidence:.2f})"
            )
        return f"blocked (score={self.result.score:.2f})"


_default_pipeline: DetectionPipeline | None = None


def build_default_pipeline(use_judge: bool | None = None) -> DetectionPipeline:
    """Lazily construct (and cache) a DetectionPipeline from current settings.

    Subsequent calls return the same instance unless explicitly invalidated by
    passing a fresh pipeline to an integration's constructor. The judge is
    enabled if either `use_judge=True` is passed OR `MITHRIL_JUDGE_ENABLED` is
    true in settings.
    """
    global _default_pipeline
    if _default_pipeline is not None:
        return _default_pipeline

    should_judge = use_judge if use_judge is not None else settings.judge_enabled
    judge = build_judge(settings) if should_judge else None
    pipeline = default_pipeline(threshold=settings.threshold, judge=judge)
    pipeline.judge_low = settings.judge_low_threshold
    pipeline.judge_high = settings.judge_high_threshold
    pipeline.fail_mode = settings.judge_fail_mode
    _default_pipeline = pipeline
    return pipeline


def reset_default_pipeline() -> None:
    """Drop the cached default pipeline. Useful in tests."""
    global _default_pipeline
    _default_pipeline = None


def extract_message_texts(messages: Sequence[Any]) -> list[str]:
    """Flatten a list of chat messages (any common shape) to plain strings.

    Accepts:
      - dicts with a `content` key (OpenAI / LiteLLM shape)
      - objects with `.content` attribute (LangChain BaseMessage)
      - tuples like `("user", "text")` (LangChain shorthand)
      - plain strings
    """
    out: list[str] = []
    for m in messages:
        if isinstance(m, str):
            out.append(m)
            continue
        if isinstance(m, dict):
            content = m.get("content")
        else:
            content = getattr(m, "content", None)
            if content is None and isinstance(m, tuple) and len(m) >= 2:
                content = m[1]

        if isinstance(content, str):
            out.append(content)
        elif isinstance(content, list):
            # OpenAI-vision-style list of parts
            parts: list[str] = []
            for chunk in content:
                if isinstance(chunk, dict):
                    t = chunk.get("text") or chunk.get("content")
                    if isinstance(t, str):
                        parts.append(t)
                elif isinstance(chunk, str):
                    parts.append(chunk)
            if parts:
                out.append("\n".join(parts))
    return out
