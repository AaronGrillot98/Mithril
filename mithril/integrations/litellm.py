"""LiteLLM integration.

Two ways to use Mithril with LiteLLM:

1. **Drop-in `completion()` / `acompletion()`** (recommended). Replace your
   `from litellm import completion` with `from mithril.integrations.litellm
   import completion`. Mithril scans the messages, and if they pass, the
   call is forwarded to LiteLLM untouched.

       from mithril.integrations.litellm import completion

       resp = completion(
           model="gpt-4o-mini",
           messages=[{"role": "user", "content": "What's the weather?"}],
       )

       # Raises MithrilBlocked:
       completion(model="gpt-4o-mini", messages=[
           {"role": "user", "content": "Ignore previous instructions"}
       ])

2. **As a callback / custom logger**. LiteLLM supports CustomLogger
   subclasses that can run during the call lifecycle.

       import litellm
       from mithril.integrations.litellm import MithrilGate

       litellm.callbacks = [MithrilGate()]   # raises on attack during pre-call

   Note: with a callback you lose strict gating in some code paths;
   prefer the drop-in approach when you can change the import.
"""

from __future__ import annotations

from typing import Any

from mithril.detectors.pipeline import DetectionPipeline
from mithril.integrations._shared import (
    MithrilBlocked,
    build_default_pipeline,
    extract_message_texts,
)


def _require_litellm():
    try:
        import litellm  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "LiteLLM integration requires `litellm`. "
            "Install with: pip install mithril-llm[litellm]"
        ) from exc
    return litellm


def _scan_kwargs(
    pipeline: DetectionPipeline | None,
    use_judge: bool,
    kwargs: dict[str, Any],
) -> None:
    """Scan the `messages` from a LiteLLM-style kwargs dict and raise on attack."""
    pipe = pipeline or build_default_pipeline(use_judge=use_judge)
    messages = kwargs.get("messages")
    if not messages:
        return
    texts = extract_message_texts(messages)
    result = pipe.scan_messages(texts)
    if result.blocked:
        raise MithrilBlocked(result)


async def _ascan_kwargs(
    pipeline: DetectionPipeline | None,
    use_judge: bool,
    kwargs: dict[str, Any],
) -> None:
    """Async variant — runs the judge layer when enabled."""
    pipe = pipeline or build_default_pipeline(use_judge=use_judge)
    messages = kwargs.get("messages")
    if not messages:
        return
    texts = extract_message_texts(messages)
    if use_judge:
        result = await pipe.evaluate_messages(texts)
    else:
        result = pipe.scan_messages(texts)
    if result.blocked:
        raise MithrilBlocked(result)


def completion(
    *args: Any,
    _mithril_pipeline: DetectionPipeline | None = None,
    _mithril_judge: bool = False,
    **kwargs: Any,
) -> Any:
    """Drop-in replacement for `litellm.completion` with Mithril scanning.

    Identical positional and keyword arguments to LiteLLM's own `completion`.
    Two extra keyword-only hooks:

      _mithril_pipeline: pre-built DetectionPipeline (advanced).
      _mithril_judge:    if True, enable the async LLM-judge layer.

    Raises:
        MithrilBlocked: when the prompt fails the firewall.
    """
    litellm = _require_litellm()
    _scan_kwargs(_mithril_pipeline, _mithril_judge, kwargs)
    return litellm.completion(*args, **kwargs)


async def acompletion(
    *args: Any,
    _mithril_pipeline: DetectionPipeline | None = None,
    _mithril_judge: bool = False,
    **kwargs: Any,
) -> Any:
    """Async drop-in replacement for `litellm.acompletion` with Mithril scanning."""
    litellm = _require_litellm()
    await _ascan_kwargs(_mithril_pipeline, _mithril_judge, kwargs)
    return await litellm.acompletion(*args, **kwargs)


def _build_gate_class():
    _require_litellm()  # surfaces a helpful ImportError if litellm is missing
    try:
        # litellm.integrations.custom_logger is the canonical path
        from litellm.integrations.custom_logger import CustomLogger  # type: ignore
    except ImportError:  # pragma: no cover
        CustomLogger = object  # type: ignore

    class MithrilGate(CustomLogger):  # type: ignore[misc]
        """LiteLLM CustomLogger that raises on attacks during the pre-call hook."""

        def __init__(
            self,
            pipeline: DetectionPipeline | None = None,
            *,
            use_judge: bool = False,
        ):
            super().__init__()
            self._pipeline = pipeline or build_default_pipeline(use_judge=use_judge)
            self._use_judge = use_judge

        def log_pre_api_call(self, model: str, messages: list[dict[str, Any]], kwargs: dict[str, Any]) -> None:
            texts = extract_message_texts(messages or [])
            result = self._pipeline.scan_messages(texts)
            if result.blocked:
                raise MithrilBlocked(result)

        async def async_log_pre_api_call(self, model: str, messages: list[dict[str, Any]], kwargs: dict[str, Any]) -> None:
            texts = extract_message_texts(messages or [])
            if self._use_judge:
                result = await self._pipeline.evaluate_messages(texts)
            else:
                result = self._pipeline.scan_messages(texts)
            if result.blocked:
                raise MithrilBlocked(result)

    return MithrilGate


def __getattr__(name: str):
    if name == "MithrilGate":
        return _build_gate_class()
    raise AttributeError(name)


__all__ = [
    "completion",
    "acompletion",
    "MithrilGate",  # noqa: F822 — provided via __getattr__
    "MithrilBlocked",
]
