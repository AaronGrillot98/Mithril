"""OpenAI-compatible chat-completions judge.

Works with any provider that implements `POST /v1/chat/completions`:
OpenAI, Together, Groq, Fireworks, Ollama, vLLM, LM Studio, llama.cpp,
Anthropic via its OpenAI-compat shim, etc.

The judge is intentionally a separate HTTP client from the proxy's upstream
forwarder so it can target a *different* model than the one being protected.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from mithril.judges.base import Judge, build_judge_messages
from mithril.models import JudgeVerdict


_JSON_OBJ = re.compile(r"\{.*?\}", re.DOTALL)


class OpenAICompatibleJudge(Judge):
    name = "openai_compat"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 5.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def verdict(self, text: str) -> JudgeVerdict:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": build_judge_messages(text),
            "temperature": 0.0,
            "max_tokens": 80,
            "response_format": {"type": "json_object"},
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        t0 = time.perf_counter()
        try:
            resp = await self._client.post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            return JudgeVerdict(
                verdict="error",
                confidence=0.0,
                reason=f"transport error: {type(exc).__name__}",
                model=self.model,
                latency_ms=(time.perf_counter() - t0) * 1000,
            )

        latency_ms = (time.perf_counter() - t0) * 1000

        # Some providers (Ollama, older shims) reject `response_format`. Retry
        # without it on a 400.
        if resp.status_code == 400 and "response_format" in body:
            body.pop("response_format", None)
            try:
                resp = await self._client.post(
                    f"{self.base_url}/chat/completions",
                    json=body,
                    headers=headers,
                )
                latency_ms = (time.perf_counter() - t0) * 1000
            except httpx.HTTPError as exc:
                return JudgeVerdict(
                    verdict="error",
                    confidence=0.0,
                    reason=f"transport error on retry: {type(exc).__name__}",
                    model=self.model,
                    latency_ms=latency_ms,
                )

        if resp.status_code >= 400:
            return JudgeVerdict(
                verdict="error",
                confidence=0.0,
                reason=f"upstream status {resp.status_code}",
                model=self.model,
                latency_ms=latency_ms,
            )

        try:
            payload = resp.json()
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            return JudgeVerdict(
                verdict="error",
                confidence=0.0,
                reason=f"malformed response: {type(exc).__name__}",
                model=self.model,
                latency_ms=latency_ms,
            )

        return self._parse(content, latency_ms)

    def _parse(self, content: str, latency_ms: float) -> JudgeVerdict:
        """Extract a JudgeVerdict from the judge's raw text response."""
        if not isinstance(content, str):
            return JudgeVerdict(
                verdict="error",
                confidence=0.0,
                reason="non-string content",
                model=self.model,
                latency_ms=latency_ms,
            )

        # First try the whole thing as JSON; if that fails, grab the first
        # {...} block (some models still wrap in prose despite instructions).
        data: dict[str, Any] | None = None
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            match = _JSON_OBJ.search(content)
            if match:
                try:
                    data = json.loads(match.group(0))
                except json.JSONDecodeError:
                    data = None

        if not isinstance(data, dict):
            return JudgeVerdict(
                verdict="error",
                confidence=0.0,
                reason="judge returned non-JSON output",
                model=self.model,
                latency_ms=latency_ms,
            )

        raw_verdict = str(data.get("verdict", "")).lower().strip()
        verdict = "attack" if raw_verdict == "attack" else "benign"

        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        reason = str(data.get("reason", ""))[:240]

        return JudgeVerdict(
            verdict=verdict,
            confidence=confidence,
            reason=reason,
            model=self.model,
            latency_ms=latency_ms,
        )
