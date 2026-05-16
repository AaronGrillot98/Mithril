"""Upstream forwarding to the configured LLM provider."""

from __future__ import annotations

from typing import Any

import httpx


class UpstreamClient:
    """Thin wrapper around httpx.AsyncClient targeting an OpenAI-compatible API."""

    def __init__(self, base_url: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def forward_chat(
        self,
        body: dict[str, Any],
        headers: dict[str, str],
    ) -> httpx.Response:
        return await self._client.post(
            f"{self.base_url}/chat/completions",
            json=body,
            headers=self._filter_headers(headers),
        )

    async def forward_stream(
        self,
        body: dict[str, Any],
        headers: dict[str, str],
    ) -> httpx.Response:
        req = self._client.build_request(
            "POST",
            f"{self.base_url}/chat/completions",
            json=body,
            headers=self._filter_headers(headers),
        )
        return await self._client.send(req, stream=True)

    @staticmethod
    def _filter_headers(incoming: dict[str, str]) -> dict[str, str]:
        # Forward only auth-related and content headers; drop hop-by-hop ones.
        keep = {"authorization", "content-type", "openai-organization", "openai-project"}
        return {k: v for k, v in incoming.items() if k.lower() in keep}
