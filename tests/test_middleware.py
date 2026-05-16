"""Tests for the request-ID middleware."""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from mithril.middleware import REQUEST_ID_HEADER, RequestIDMiddleware


@pytest.fixture
def app():
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/echo")
    async def echo(request: Request) -> dict[str, str]:
        return {"id": request.state.request_id}

    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_response_includes_request_id_header(client):
    r = client.get("/echo")
    assert r.status_code == 200
    assert REQUEST_ID_HEADER in r.headers
    # 32 hex chars (UUID4.hex)
    assert len(r.headers[REQUEST_ID_HEADER]) == 32


def test_request_id_echoes_inbound_when_present(client):
    r = client.get("/echo", headers={REQUEST_ID_HEADER: "my-trace-id-123"})
    assert r.status_code == 200
    assert r.headers[REQUEST_ID_HEADER] == "my-trace-id-123"
    assert r.json()["id"] == "my-trace-id-123"


def test_request_id_is_unique_per_request(client):
    seen = {client.get("/echo").headers[REQUEST_ID_HEADER] for _ in range(5)}
    assert len(seen) == 5


def test_access_log_emitted_on_every_request(client, caplog):
    with caplog.at_level(logging.INFO, logger="mithril.access"):
        client.get("/echo", headers={REQUEST_ID_HEADER: "trace-abc"})
    matching = [r for r in caplog.records if "trace-abc" in r.getMessage()]
    assert len(matching) == 1
    msg = matching[0].getMessage()
    assert "GET" in msg
    assert "/echo" in msg
    assert "200" in msg
    # Should also include a duration in ms.
    assert "ms" in msg


def test_access_log_records_status_when_handler_raises(caplog):
    """When the route raises, the access log still gets a final entry — important
    for tracing failures back through correlation IDs in production.

    Note: the response itself comes from FastAPI's default exception handler,
    which runs *after* our middleware. So the X-Request-ID won't appear on
    the error response body's headers — but the log line will still be there.
    """
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("expected explosion")

    with TestClient(app, raise_server_exceptions=False) as client:
        with caplog.at_level(logging.INFO, logger="mithril.access"):
            client.get("/boom", headers={REQUEST_ID_HEADER: "trace-boom"})
    matching = [r for r in caplog.records if "trace-boom" in r.getMessage()]
    assert len(matching) == 1
    msg = matching[0].getMessage()
    # The middleware records the status it saw — when the handler raises,
    # our default is the sentinel 500.
    assert "500" in msg
    assert "/boom" in msg


def test_request_state_request_id_accessible_in_handler(client):
    r = client.get("/echo", headers={REQUEST_ID_HEADER: "id-in-state"})
    assert r.json()["id"] == "id-in-state"
