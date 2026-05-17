"""Redaction helpers for output scanning.

Replaces the character span of each finding with a stable marker like
``[REDACTED:PII003]``. When findings overlap (e.g. one rule fires inside
another's span), the **longer** span wins — that's the safe default for
secret material.
"""

from __future__ import annotations

from mithril.models import Finding


def redact(text: str, findings: list[Finding], marker_format: str = "[REDACTED:{rule_id}]") -> str:
    """Rewrite ``text`` replacing each finding's span with a redaction marker.

    Args:
        text: The original text to redact.
        findings: Findings with valid start/end character offsets.
        marker_format: Template used for replacement. Available substitutions:
            ``{rule_id}``  — the detector rule that fired (default).
            ``{detector}`` — the detector name (e.g. ``pii``).
            ``{severity}`` — the severity tag.

    When two findings overlap, the **longer** span is preserved and the
    nested one is dropped. Findings with ``end <= start`` (e.g. legacy
    Finding instances built before v0.4) are silently skipped.
    """
    if not findings:
        return text

    # Normalize and prepare spans.
    spans: list[tuple[int, int, str]] = []
    for f in findings:
        if f.end <= f.start:
            continue
        start = max(0, min(f.start, len(text)))
        end = max(start, min(f.end, len(text)))
        if end <= start:
            continue
        marker = marker_format.format(
            rule_id=f.rule_id, detector=f.detector, severity=f.severity
        )
        spans.append((start, end, marker))

    if not spans:
        return text

    # Step 1: pick non-overlapping spans, longest first. This guarantees that
    # when a smaller match is nested inside a larger one, the larger one wins.
    spans.sort(key=lambda s: (-(s[1] - s[0]), s[0]))
    picked: list[tuple[int, int, str]] = []
    for start, end, marker in spans:
        if any(start < pend and end > pstart for pstart, pend, _ in picked):
            continue
        picked.append((start, end, marker))

    # Step 2: replace right-to-left so earlier offsets aren't invalidated.
    picked.sort(key=lambda s: -s[0])
    result = text
    for start, end, marker in picked:
        result = result[:start] + marker + result[end:]
    return result
