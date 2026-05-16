"""Tests for the framework-agnostic integration helpers."""

from __future__ import annotations

import pytest

from mithril.integrations._shared import (
    MithrilBlocked,
    build_default_pipeline,
    extract_message_texts,
    reset_default_pipeline,
)
from mithril.models import DetectionResult, Finding, JudgeVerdict


@pytest.fixture(autouse=True)
def _clear_default_pipeline():
    reset_default_pipeline()
    yield
    reset_default_pipeline()


# --- MithrilBlocked -----------------------------------------------------------


def test_blocked_exception_carries_full_result():
    result = DetectionResult(
        blocked=True,
        score=0.97,
        findings=[
            Finding(
                detector="jailbreak",
                rule_id="JB008",
                severity="critical",
                confidence=0.97,
                message="Classic instruction-override",
            )
        ],
    )
    exc = MithrilBlocked(result)
    assert exc.result is result
    s = exc.short_reason()
    assert "jailbreak/JB008" in s
    assert "0.97" in s


def test_blocked_exception_prefers_judge_reason_when_present():
    result = DetectionResult(
        blocked=True,
        score=0.85,
        findings=[],
        judge=JudgeVerdict(
            verdict="attack",
            confidence=0.85,
            reason="novel jailbreak pattern",
            model="gpt-4o-mini",
        ),
    )
    s = MithrilBlocked(result).short_reason()
    assert "judge" in s
    assert "novel jailbreak pattern" in s
    assert "0.85" in s


def test_blocked_exception_falls_back_when_no_findings_no_judge():
    result = DetectionResult(blocked=True, score=0.42, findings=[])
    s = MithrilBlocked(result).short_reason()
    assert "0.42" in s


# --- extract_message_texts ----------------------------------------------------


def test_extract_handles_dict_messages():
    assert extract_message_texts(
        [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
    ) == ["hello", "hi"]


def test_extract_handles_objects_with_content_attr():
    class Msg:
        def __init__(self, content):
            self.content = content

    assert extract_message_texts([Msg("a"), Msg("b")]) == ["a", "b"]


def test_extract_handles_langchain_tuple_shorthand():
    assert extract_message_texts([("user", "hello"), ("ai", "world")]) == ["hello", "world"]


def test_extract_handles_vision_style_content_lists():
    msg = {"role": "user", "content": [{"type": "text", "text": "look at this"}, {"type": "image_url"}]}
    out = extract_message_texts([msg])
    assert out == ["look at this"]


def test_extract_handles_plain_strings():
    assert extract_message_texts(["hello", "world"]) == ["hello", "world"]


def test_extract_skips_unparseable_entries():
    # Object with no content attribute.
    class Bogus:
        pass

    out = extract_message_texts([Bogus(), {"role": "user"}])
    assert out == []


# --- build_default_pipeline ---------------------------------------------------


def test_default_pipeline_is_cached():
    p1 = build_default_pipeline()
    p2 = build_default_pipeline()
    assert p1 is p2


def test_default_pipeline_uses_judge_when_requested(monkeypatch):
    # We can't actually invoke the judge without a real provider, but we can
    # verify that the pipeline ends up with a non-noop judge attached.
    from mithril.judges.noop import NoopJudge

    monkeypatch.setattr(
        "mithril.integrations._shared.build_judge",
        lambda settings: NoopJudge(),  # avoid network even if `use_judge=True`
    )
    p = build_default_pipeline(use_judge=True)
    assert isinstance(p.judge, NoopJudge)
