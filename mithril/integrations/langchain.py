"""LangChain integration.

Two ways to use Mithril with LangChain:

1. **As a Runnable wrapper** (recommended). Wrap any chat model in
   `MithrilGuard` and it scans every input before forwarding to the
   underlying LLM. Raises `MithrilBlocked` on attacks.

       from langchain_openai import ChatOpenAI
       from mithril.integrations.langchain import MithrilGuard

       llm = ChatOpenAI(model="gpt-4o-mini")
       guarded = MithrilGuard(llm)
       guarded.invoke("What's the capital of France?")          # passes
       guarded.invoke("Ignore previous instructions and ...")   # raises MithrilBlocked

   `MithrilGuard` is itself a Runnable, so it composes with LCEL:

       chain = prompt | MithrilGuard(llm) | parser

2. **As a callback handler**. If you can't wrap the model (e.g., you're
   inside a framework that constructs it for you), attach
   `MithrilCallbackHandler` and raise on attacks via `on_chat_model_start`
   / `on_llm_start`.

       from mithril.integrations.langchain import MithrilCallbackHandler
       llm = ChatOpenAI(callbacks=[MithrilCallbackHandler()])
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Iterator

from mithril.detectors.pipeline import DetectionPipeline
from mithril.integrations._shared import (
    MithrilBlocked,
    build_default_pipeline,
    extract_message_texts,
)


# Lazy imports — we don't want `mithril` to depend on langchain.

def _require_langchain():
    try:
        from langchain_core.runnables import Runnable
        from langchain_core.callbacks import BaseCallbackHandler
        from langchain_core.messages import BaseMessage
        from langchain_core.prompt_values import PromptValue
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "LangChain integration requires `langchain-core`. "
            "Install with: pip install mithril-llm[langchain]"
        ) from exc
    return Runnable, BaseCallbackHandler, BaseMessage, PromptValue


def _coerce_to_texts(input_value: Any) -> list[str]:
    """Best-effort text extraction from any common LangChain input shape."""
    # Plain string
    if isinstance(input_value, str):
        return [input_value]

    # PromptValue → string or messages
    to_string = getattr(input_value, "to_string", None)
    if callable(to_string):
        return [to_string()]

    # List of messages (BaseMessage objects, tuples, or dicts)
    if isinstance(input_value, list):
        return extract_message_texts(input_value)

    # dict with messages key
    if isinstance(input_value, dict):
        messages = input_value.get("messages") or input_value.get("input")
        if isinstance(messages, list):
            return extract_message_texts(messages)
        text = input_value.get("input") or input_value.get("text") or input_value.get("content")
        if isinstance(text, str):
            return [text]
        return [str(input_value)]

    # Single message-like object with `.content`
    content = getattr(input_value, "content", None)
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return extract_message_texts([input_value])

    # Fallback — best-effort stringification.
    return [str(input_value)]


def _build_guard_class():
    Runnable, _BCH, _BM, _PV = _require_langchain()

    class MithrilGuard(Runnable):
        """Runnable wrapper that scans inputs before forwarding to an LLM.

        Args:
            llm: Any Runnable. Typically a ChatModel.
            pipeline: Optional pre-built DetectionPipeline. Defaults to the
                Mithril singleton built from settings.
            use_judge: If True (and no pipeline is passed), enable the
                async LLM-judge layer when constructing the default.
        """

        def __init__(
            self,
            llm: Any,
            pipeline: DetectionPipeline | None = None,
            *,
            use_judge: bool = False,
        ):
            self._llm = llm
            self._pipeline = pipeline or build_default_pipeline(use_judge=use_judge)
            self._use_judge = use_judge or pipeline is not None  # if user passed pipeline, trust it

        @property
        def InputType(self) -> Any:  # type: ignore[override]
            return getattr(self._llm, "InputType", Any)

        @property
        def OutputType(self) -> Any:  # type: ignore[override]
            return getattr(self._llm, "OutputType", Any)

        def _scan(self, input_value: Any) -> None:
            texts = _coerce_to_texts(input_value)
            result = self._pipeline.scan_messages(texts)
            if result.blocked:
                raise MithrilBlocked(result)

        async def _scan_async(self, input_value: Any) -> None:
            texts = _coerce_to_texts(input_value)
            if self._use_judge:
                result = await self._pipeline.evaluate_messages(texts)
            else:
                result = self._pipeline.scan_messages(texts)
            if result.blocked:
                raise MithrilBlocked(result)

        # --- Runnable surface ---------------------------------------------

        def invoke(self, input: Any, config: Any | None = None, **kwargs: Any) -> Any:  # noqa: A002
            self._scan(input)
            return self._llm.invoke(input, config=config, **kwargs)

        async def ainvoke(self, input: Any, config: Any | None = None, **kwargs: Any) -> Any:  # noqa: A002
            await self._scan_async(input)
            return await self._llm.ainvoke(input, config=config, **kwargs)

        def batch(self, inputs: list[Any], config: Any | None = None, **kwargs: Any) -> list[Any]:
            for i in inputs:
                self._scan(i)
            return self._llm.batch(inputs, config=config, **kwargs)

        async def abatch(self, inputs: list[Any], config: Any | None = None, **kwargs: Any) -> list[Any]:
            for i in inputs:
                await self._scan_async(i)
            return await self._llm.abatch(inputs, config=config, **kwargs)

        def stream(self, input: Any, config: Any | None = None, **kwargs: Any) -> Iterator[Any]:  # noqa: A002
            self._scan(input)
            yield from self._llm.stream(input, config=config, **kwargs)

        async def astream(self, input: Any, config: Any | None = None, **kwargs: Any) -> AsyncIterator[Any]:  # noqa: A002
            await self._scan_async(input)
            async for chunk in self._llm.astream(input, config=config, **kwargs):
                yield chunk

    return MithrilGuard


def _build_callback_class():
    _R, BaseCallbackHandler, _BM, _PV = _require_langchain()

    class MithrilCallbackHandler(BaseCallbackHandler):
        """Callback that scans every prompt before the LLM call and raises on attacks.

        Note: LangChain callbacks are observers by convention. Raising from a
        callback DOES halt the LLM call (LangChain wraps callbacks in try/except
        but re-raises by default unless the host disabled error propagation).
        Prefer `MithrilGuard` for production — it composes cleanly into LCEL.
        """

        def __init__(
            self,
            pipeline: DetectionPipeline | None = None,
            *,
            use_judge: bool = False,
        ):
            super().__init__()
            self._pipeline = pipeline or build_default_pipeline(use_judge=use_judge)
            self._use_judge = use_judge

        def on_llm_start(self, serialized: Any, prompts: list[str], **kwargs: Any) -> None:
            result = self._pipeline.scan_messages(prompts)
            if result.blocked:
                raise MithrilBlocked(result)

        def on_chat_model_start(self, serialized: Any, messages: Any, **kwargs: Any) -> None:
            # `messages` is list[list[BaseMessage]] (one inner list per chat).
            flat: list[str] = []
            for batch in messages:
                flat.extend(extract_message_texts(batch))
            result = self._pipeline.scan_messages(flat)
            if result.blocked:
                raise MithrilBlocked(result)

    return MithrilCallbackHandler


# Public lazy attributes.
def __getattr__(name: str):
    if name == "MithrilGuard":
        return _build_guard_class()
    if name == "MithrilCallbackHandler":
        return _build_callback_class()
    raise AttributeError(name)


# Lazy exports — these are constructed on demand via __getattr__ so we don't
# import langchain at module load. Ruff doesn't see the __getattr__ pattern.
__all__ = [
    "MithrilGuard",            # noqa: F822 — provided via __getattr__
    "MithrilCallbackHandler",  # noqa: F822 — provided via __getattr__
    "MithrilBlocked",
]
