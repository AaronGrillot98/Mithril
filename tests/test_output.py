"""Tests for output scanning (v0.4).

Three layers:

  1. ``redact()`` — pure function, span rewriting.
  2. ``OutputScanner`` — orchestrates detectors + redactor under the three
     modes (block / redact / log).
  3. Server integration — non-streaming and streaming proxied responses
     scanned via the FastAPI app.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from mithril.models import Finding, OutputScanResult
from mithril.output import OutputScanner, default_output_scanner, redact


# --- redactor (pure) ---------------------------------------------------------


def _finding(rule_id: str, start: int, end: int, *, severity: str = "critical") -> Finding:
    return Finding(
        detector="pii",
        rule_id=rule_id,
        severity=severity,
        confidence=0.98,
        message="test",
        excerpt="",
        start=start,
        end=end,
    )


def test_redact_single_span():
    text = "My SSN is 123-45-6789, that's my secret."
    f = _finding("PII001", 10, 21)
    out = redact(text, [f])
    assert out == "My SSN is [REDACTED:PII001], that's my secret."


def test_redact_multiple_non_overlapping_spans():
    text = "Key A: sk-aaaaaaaaaaaaaaaaaaaa, Key B: sk-bbbbbbbbbbbbbbbbbbbb"
    findings = [_finding("PII003", 7, 30), _finding("PII003", 39, 62)]
    out = redact(text, findings)
    assert out == "Key A: [REDACTED:PII003], Key B: [REDACTED:PII003]"


def test_redact_returns_text_unchanged_when_no_findings():
    assert redact("hello world", []) == "hello world"


def test_redact_ignores_degenerate_spans():
    text = "hello"
    f = _finding("X", 3, 3)  # zero-width
    assert redact(text, [f]) == "hello"


def test_redact_custom_marker_format():
    text = "secret: ABCDEFGHIJ"
    f = _finding("SEC001", 8, 18, severity="high")
    out = redact(text, [f], marker_format="<<{detector}:{rule_id}:{severity}>>")
    assert out == "secret: <<pii:SEC001:high>>"


def test_redact_handles_overlapping_spans_by_keeping_longer_first():
    """If two findings overlap (e.g. credit-card pattern + SSN pattern fragments)
    we keep the larger span to be safe."""
    text = "1234-56-78901234567"
    longer = _finding("PII002", 0, 19)
    shorter = _finding("PII001", 5, 14)  # nested
    out = redact(text, [longer, shorter])
    # The longer span is preserved.
    assert "[REDACTED:PII002]" in out


# --- OutputScanner: modes ----------------------------------------------------


@pytest.fixture
def scanner_block() -> OutputScanner:
    return default_output_scanner(mode="block")


@pytest.fixture
def scanner_redact() -> OutputScanner:
    return default_output_scanner(mode="redact")


@pytest.fixture
def scanner_log() -> OutputScanner:
    return default_output_scanner(mode="log")


def test_clean_response_is_allowed_in_all_modes(scanner_block, scanner_redact, scanner_log):
    text = "The capital of France is Paris."
    for scanner in (scanner_block, scanner_redact, scanner_log):
        result = scanner.scan(text)
        assert result.action == "allow"
        assert result.findings == []
        assert result.redacted_text is None


def test_pii_in_response_blocks_in_block_mode(scanner_block):
    result = scanner_block.scan("Sure! Your saved SSN is 123-45-6789.")
    assert result.action == "block"
    assert result.score >= 0.7
    assert any(f.rule_id == "PII001" for f in result.findings)
    assert result.redacted_text is None


def test_pii_in_response_redacts_in_redact_mode(scanner_redact):
    result = scanner_redact.scan("Sure! Your saved SSN is 123-45-6789.")
    assert result.action == "redact"
    assert result.redacted_text == "Sure! Your saved SSN is [REDACTED:PII001]."


def test_pii_in_response_logs_in_log_mode(scanner_log):
    """log mode flags findings but does NOT alter the response."""
    result = scanner_log.scan("Sure! Your saved SSN is 123-45-6789.")
    assert result.action == "allow"  # passthrough — caller forwards as-is
    assert result.findings  # but findings are recorded for audit
    assert result.redacted_text is None


def test_openai_key_in_response_is_redacted(scanner_redact):
    leaked = "Here's the key you asked me to remember: sk-EXAMPLEDUMMYNOTAREALKEY1234567890"
    result = scanner_redact.scan(leaked)
    assert result.action == "redact"
    assert "[REDACTED:PII003]" in result.redacted_text
    # The original key is not present in the redacted text.
    assert "sk-EXAMPLEDUMMYNOTAREALKEY1234567890" not in result.redacted_text


def test_below_threshold_findings_pass_through_with_low_severity_visible():
    """Confidence 0.6 from PII002 alone should NOT trip the default 0.7 threshold."""
    scanner = default_output_scanner(threshold=0.7)
    # A long stretch of digits triggers PII002 (credit-card-shaped) at 0.6.
    result = scanner.scan("Random tracking number 1234567890123456")
    # If it triggered: action would be redact. We expect allow.
    if result.findings:
        assert result.score < 0.7
    assert result.action == "allow"


def test_jailbreak_vocab_in_response_does_not_fire():
    """The output scanner deliberately omits jailbreak/role-hijack/prompt-leak
    detectors — those target attacker technique in user inputs, not content
    in model responses. A model legitimately discussing 'ignore previous
    instructions' as a topic must not trip the output filter."""
    scanner = default_output_scanner(mode="block")
    result = scanner.scan(
        'A "jailbreak prompt" is one that says "ignore previous instructions" — '
        "the most common attack pattern in the OWASP LLM Top 10."
    )
    assert result.action == "allow"


# --- server integration ------------------------------------------------------


@pytest.fixture
def server_client(tmp_path, monkeypatch):
    """Boot the FastAPI app with output scanning enabled."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("MITHRIL_DB_PATH", str(tmp_path / "m.db"))
    monkeypatch.setenv("MITHRIL_OUTPUT_SCAN_ENABLED", "true")
    monkeypatch.setenv("MITHRIL_OUTPUT_SCAN_MODE", "redact")

    from mithril import config as _config
    from mithril.config import Settings

    fresh = Settings()
    monkeypatch.setattr(_config, "settings", fresh)
    from mithril import server as _server

    monkeypatch.setattr(_server, "settings", fresh)

    with TestClient(_server.app) as c:
        yield c


def _stub_upstream(client: TestClient, responder) -> None:
    client.app.state.upstream._client._transport = httpx.MockTransport(responder)


def test_server_redacts_pii_in_response(server_client):
    """The clean prompt passes through, the response contains an SSN, scanner redacts."""
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "cmpl-1",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Your SSN is 123-45-6789. Don't share it.",
                        }
                    }
                ],
            },
        )

    _stub_upstream(server_client, responder)

    r = server_client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "What is my SSN?"}],
        },
        headers={"Authorization": "Bearer sk-fake"},
    )
    assert r.status_code == 200
    body = r.json()
    content = body["choices"][0]["message"]["content"]
    assert "[REDACTED:PII001]" in content
    assert "123-45-6789" not in content


def test_server_passes_clean_response_unchanged(server_client):
    def responder(request):
        return httpx.Response(
            200,
            json={
                "id": "cmpl-2",
                "choices": [
                    {"message": {"role": "assistant", "content": "Paris is the capital of France."}}
                ],
            },
        )

    _stub_upstream(server_client, responder)
    r = server_client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Capital?"}]},
        headers={"Authorization": "Bearer sk-fake"},
    )
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "Paris is the capital of France."


def test_server_rejects_oversized_non_streaming_response(tmp_path, monkeypatch):
    """When upstream returns more than MITHRIL_MAX_RESPONSE_BYTES, scanning is
    refused with a 502 — protects the proxy from OOM via runaway responses."""
    monkeypatch.setenv("MITHRIL_DB_PATH", str(tmp_path / "m.db"))
    monkeypatch.setenv("MITHRIL_OUTPUT_SCAN_ENABLED", "true")
    monkeypatch.setenv("MITHRIL_OUTPUT_SCAN_MODE", "redact")
    monkeypatch.setenv("MITHRIL_MAX_RESPONSE_BYTES", "1024")  # 1 KiB cap

    from mithril import config as _config
    from mithril.config import Settings

    fresh = Settings()
    monkeypatch.setattr(_config, "settings", fresh)
    from mithril import server as _server

    monkeypatch.setattr(_server, "settings", fresh)

    huge = "x" * 5000  # response will be ~5 KiB, exceeds the 1 KiB cap

    def responder(request):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": huge}}]},
        )

    with TestClient(_server.app) as client:
        _stub_upstream(client, responder)
        r = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer sk-fake"},
        )
    assert r.status_code == 502
    assert r.json()["error"]["type"] == "response_too_large"


def test_server_rejects_oversized_streaming_response(tmp_path, monkeypatch):
    """Same protection for the streaming buffer-then-scan path."""
    monkeypatch.setenv("MITHRIL_DB_PATH", str(tmp_path / "m.db"))
    monkeypatch.setenv("MITHRIL_OUTPUT_SCAN_ENABLED", "true")
    monkeypatch.setenv("MITHRIL_MAX_RESPONSE_BYTES", "1024")

    from mithril import config as _config
    from mithril.config import Settings

    fresh = Settings()
    monkeypatch.setattr(_config, "settings", fresh)
    from mithril import server as _server

    monkeypatch.setattr(_server, "settings", fresh)

    # Build an SSE stream that's well over the cap.
    big_payload = "x" * 300
    payloads = [{"choices": [{"delta": {"content": big_payload}}]} for _ in range(20)]
    sse = b"".join(f"data: {json.dumps(p)}\n\n".encode("utf-8") for p in payloads)
    sse += b"data: [DONE]\n\n"

    def responder(request):
        return httpx.Response(
            200,
            content=sse,
            headers={"content-type": "text/event-stream"},
        )

    with TestClient(_server.app) as client:
        _stub_upstream(client, responder)
        r = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
            headers={"Authorization": "Bearer sk-fake"},
        )
    assert r.status_code == 502
    assert r.json()["error"]["type"] == "response_too_large"


def test_server_does_not_scan_non_200_upstream(server_client):
    """If upstream errors, we should pass that through, not run the scanner on it."""
    def responder(request):
        return httpx.Response(
            429,
            json={"error": "rate limited"},
        )

    _stub_upstream(server_client, responder)
    r = server_client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": "Bearer sk-fake"},
    )
    assert r.status_code == 429


def test_server_health_surfaces_output_scan_config(server_client):
    r = server_client.get("/health")
    j = r.json()
    assert j["output_scan"]["enabled"] is True
    assert j["output_scan"]["mode"] == "redact"


# --- output scanning with block mode in the server -------------------------


@pytest.fixture
def server_block_client(tmp_path, monkeypatch):
    monkeypatch.setenv("MITHRIL_DB_PATH", str(tmp_path / "m.db"))
    monkeypatch.setenv("MITHRIL_OUTPUT_SCAN_ENABLED", "true")
    monkeypatch.setenv("MITHRIL_OUTPUT_SCAN_MODE", "block")

    from mithril import config as _config
    from mithril.config import Settings

    fresh = Settings()
    monkeypatch.setattr(_config, "settings", fresh)
    from mithril import server as _server

    monkeypatch.setattr(_server, "settings", fresh)

    with TestClient(_server.app) as c:
        yield c


def test_server_blocks_pii_response_in_block_mode(server_block_client):
    def responder(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "SSN: 123-45-6789"}}
                ]
            },
        )

    _stub_upstream(server_block_client, responder)
    r = server_block_client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "tell me"}]},
        headers={"Authorization": "Bearer sk-fake"},
    )
    assert r.status_code == 403
    err = r.json()["error"]
    assert err["type"] == "mithril_output_blocked"
    assert any(f["rule_id"] == "PII001" for f in err["findings"])


# --- streaming ---------------------------------------------------------------


def _sse(payloads: list[dict[str, Any]]) -> bytes:
    """Build a fake SSE byte stream from a list of chunk payloads."""
    out = b""
    for p in payloads:
        out += f"data: {json.dumps(p)}\n\n".encode("utf-8")
    out += b"data: [DONE]\n\n"
    return out


def test_server_buffers_and_redacts_streaming_response(server_client):
    """Streaming + output_scan_enabled = buffer the whole stream, then scan."""
    sse_body = _sse(
        [
            {"choices": [{"delta": {"content": "Your "}}]},
            {"choices": [{"delta": {"content": "SSN is "}}]},
            {"choices": [{"delta": {"content": "123-45-6789"}}]},
            {"choices": [{"delta": {"content": "."}}]},
        ]
    )

    def responder(request):
        return httpx.Response(
            200,
            content=sse_body,
            headers={"content-type": "text/event-stream"},
        )

    _stub_upstream(server_client, responder)
    r = server_client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "tell me my SSN"}],
            "stream": True,
        },
        headers={"Authorization": "Bearer sk-fake"},
    )
    assert r.status_code == 200
    # The redacted content is emitted as a single non-chunked SSE message.
    text = r.text
    assert "[REDACTED:PII001]" in text
    assert "123-45-6789" not in text
    assert text.endswith("data: [DONE]\n\n")


def test_server_passes_clean_streaming_response_through(server_client):
    sse_body = _sse(
        [
            {"choices": [{"delta": {"content": "Paris "}}]},
            {"choices": [{"delta": {"content": "is "}}]},
            {"choices": [{"delta": {"content": "the capital."}}]},
        ]
    )

    def responder(request):
        return httpx.Response(
            200,
            content=sse_body,
            headers={"content-type": "text/event-stream"},
        )

    _stub_upstream(server_client, responder)
    r = server_client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "capital of France?"}],
            "stream": True,
        },
        headers={"Authorization": "Bearer sk-fake"},
    )
    assert r.status_code == 200
    # Original SSE chunks pass through unchanged.
    assert "Paris " in r.text
    assert "is " in r.text
    assert "the capital." in r.text


# --- backwards compatibility: scanner off by default ------------------------


def test_default_server_does_not_scan_output(tmp_path, monkeypatch):
    """When output_scan_enabled is unset, responses pass through untouched
    — exactly v0.3.x behavior."""
    monkeypatch.setenv("MITHRIL_DB_PATH", str(tmp_path / "m.db"))
    # Crucially: do NOT set MITHRIL_OUTPUT_SCAN_ENABLED.

    from mithril import config as _config
    from mithril.config import Settings

    fresh = Settings()
    assert fresh.output_scan_enabled is False
    monkeypatch.setattr(_config, "settings", fresh)
    from mithril import server as _server

    monkeypatch.setattr(_server, "settings", fresh)

    def responder(request):
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "SSN: 123-45-6789"}}]
            },
        )

    with TestClient(_server.app) as client:
        _stub_upstream(client, responder)
        r = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer sk-fake"},
        )
    # Response is forwarded unchanged — SSN is still there.
    assert r.status_code == 200
    assert "123-45-6789" in r.json()["choices"][0]["message"]["content"]


# --- pure model checks ------------------------------------------------------


def test_output_scan_result_top_severity():
    result = OutputScanResult(
        action="redact",
        score=0.98,
        findings=[
            _finding("PII001", 0, 11, severity="high"),
            _finding("PII003", 20, 50, severity="critical"),
        ],
    )
    assert result.top_severity == "critical"


def test_output_scan_result_empty_severity_is_info():
    result = OutputScanResult(action="allow", score=0.0)
    assert result.top_severity == "info"
