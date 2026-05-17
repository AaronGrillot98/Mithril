"""Tests for config validation in mithril/config.py."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mithril.config import Settings


def test_defaults_are_valid():
    s = Settings()
    assert 0.0 <= s.threshold <= 1.0
    assert s.judge_low_threshold < s.judge_high_threshold


def test_threshold_rejects_out_of_range(monkeypatch):
    monkeypatch.setenv("MITHRIL_THRESHOLD", "2.0")
    with pytest.raises(ValidationError):
        Settings()


def test_threshold_rejects_negative(monkeypatch):
    monkeypatch.setenv("MITHRIL_THRESHOLD", "-0.1")
    with pytest.raises(ValidationError):
        Settings()


def test_judge_band_inverted_rejected(monkeypatch):
    monkeypatch.setenv("MITHRIL_JUDGE_LOW_THRESHOLD", "0.9")
    monkeypatch.setenv("MITHRIL_JUDGE_HIGH_THRESHOLD", "0.2")
    with pytest.raises(ValidationError):
        Settings()


def test_judge_band_equal_rejected(monkeypatch):
    monkeypatch.setenv("MITHRIL_JUDGE_LOW_THRESHOLD", "0.5")
    monkeypatch.setenv("MITHRIL_JUDGE_HIGH_THRESHOLD", "0.5")
    with pytest.raises(ValidationError):
        Settings()


def test_port_out_of_range_rejected(monkeypatch):
    monkeypatch.setenv("MITHRIL_PORT", "70000")
    with pytest.raises(ValidationError):
        Settings()


def test_judge_timeout_must_be_positive(monkeypatch):
    monkeypatch.setenv("MITHRIL_JUDGE_TIMEOUT", "0")
    with pytest.raises(ValidationError):
        Settings()


def test_max_body_bytes_minimum_enforced(monkeypatch):
    monkeypatch.setenv("MITHRIL_MAX_BODY_BYTES", "100")
    with pytest.raises(ValidationError):
        Settings()


def test_max_response_bytes_minimum_enforced(monkeypatch):
    monkeypatch.setenv("MITHRIL_MAX_RESPONSE_BYTES", "100")
    with pytest.raises(ValidationError):
        Settings()


def test_output_scan_marker_with_malformed_placeholder_rejected(monkeypatch):
    monkeypatch.setenv("MITHRIL_OUTPUT_SCAN_MARKER", "[X:{rule_id")  # missing closing brace
    with pytest.raises(ValidationError):
        Settings()


def test_output_scan_marker_with_unknown_placeholder_rejected(monkeypatch):
    monkeypatch.setenv("MITHRIL_OUTPUT_SCAN_MARKER", "[X:{nonexistent}]")
    with pytest.raises(ValidationError):
        Settings()


def test_output_scan_marker_with_valid_placeholders_loads(monkeypatch):
    monkeypatch.setenv(
        "MITHRIL_OUTPUT_SCAN_MARKER", "<<{detector}/{rule_id} @ {severity}>>"
    )
    s = Settings()
    assert "{detector}" in s.output_scan_marker


def test_valid_custom_config_loads(monkeypatch):
    monkeypatch.setenv("MITHRIL_THRESHOLD", "0.85")
    monkeypatch.setenv("MITHRIL_JUDGE_LOW_THRESHOLD", "0.3")
    monkeypatch.setenv("MITHRIL_JUDGE_HIGH_THRESHOLD", "0.95")
    monkeypatch.setenv("MITHRIL_PORT", "9000")
    monkeypatch.setenv("MITHRIL_MODE", "log")
    s = Settings()
    assert s.threshold == 0.85
    assert s.port == 9000
    assert s.mode == "log"
