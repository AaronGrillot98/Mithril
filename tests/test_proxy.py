"""Tests for the upstream HTTP forwarder in mithril/proxy.py."""

from __future__ import annotations

import httpx
import pytest

from mithril.proxy import UpstreamClient


def _mock(client: UpstreamClient, responder) -> UpstreamClient:
    client._client._transport = httpx.MockTransport(responder)
    return client


@pytest.fixture
async def upstream():
    client = UpstreamClient("https://example.test/v1")
    yield client
    await client.aclose()


# --- successful forward -------------------------------------------------------


@pytest.mark.asyncio
async def test_forward_chat_returns_full_response(upstream):
    captured = {}

    def responder(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["headers"] = dict(req.headers)
        return httpx.Response(200, json={"echo": "ok"})

    _mock(upstream, responder)
    resp = await upstream.forward_chat(
        body={"model": "gpt-4o-mini", "messages": []},
        headers={"Authorization": "Bearer x"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"echo": "ok"}
    assert captured["url"] == "https://example.test/v1/chat/completions"


@pytest.mark.asyncio
async def test_forward_strips_host_and_cookie(upstream):
    captured = {}

    def responder(req: httpx.Request) -> httpx.Response:
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        return httpx.Response(200, json={"ok": True})

    _mock(upstream, responder)
    await upstream.forward_chat(
        body={"messages": []},
        headers={
            "Authorization": "Bearer secret",
            "Cookie": "session=abc",
            "Host": "evil.example",
        },
    )
    # Authorization survives.
    assert captured["headers"].get("authorization") == "Bearer secret"
    # Cookie does not.
    assert "cookie" not in captured["headers"]


# --- error paths --------------------------------------------------------------


@pytest.mark.asyncio
async def test_forward_chat_propagates_http_errors(upstream):
    def responder(req):
        raise httpx.ConnectError("nope")

    _mock(upstream, responder)
    with pytest.raises(httpx.ConnectError):
        await upstream.forward_chat(body={"messages": []}, headers={})


@pytest.mark.asyncio
async def test_forward_chat_does_not_raise_on_4xx_or_5xx(upstream):
    """The upstream raising httpx error is one thing — but a 502 with body is
    a normal HTTP response that callers handle. Confirm it does NOT raise."""
    def responder(req):
        return httpx.Response(
            502,
            content=b"<html>upstream is sad</html>",
            headers={"content-type": "text/html"},
        )

    _mock(upstream, responder)
    resp = await upstream.forward_chat(body={"messages": []}, headers={})
    assert resp.status_code == 502
    assert b"sad" in resp.content


# --- header filter ------------------------------------------------------------


def test_filter_headers_keeps_only_safe_keys():
    raw = {
        "Authorization": "Bearer x",
        "Content-Type": "application/json",
        "openai-organization": "org-1",
        "Cookie": "leak=me",
        "Host": "evil",
        "X-Custom": "should-drop",
    }
    kept = UpstreamClient._filter_headers(raw)
    assert "authorization" in {k.lower() for k in kept}
    assert "content-type" in {k.lower() for k in kept}
    assert "openai-organization" in {k.lower() for k in kept}
    assert "cookie" not in {k.lower() for k in kept}
    assert "host" not in {k.lower() for k in kept}
    assert "x-custom" not in {k.lower() for k in kept}


@pytest.mark.asyncio
async def test_aclose_is_idempotent(upstream):
    await upstream.aclose()
    await upstream.aclose()  # should not raise
