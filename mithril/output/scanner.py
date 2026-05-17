"""OutputScanner — Mithril's response-side firewall."""

from __future__ import annotations

from typing import Literal

from mithril.detectors.base import Detector
from mithril.detectors.heuristics import PIIDetector, SecretsDetector
from mithril.models import Finding, OutputScanResult
from mithril.output.redactor import redact


OutputMode = Literal["block", "redact", "log"]


class OutputScanner:
    """Scan an LLM response for PII / secrets / credentials.

    Decoupled from the input-side ``DetectionPipeline`` on purpose — the
    detector subsets are different (no jailbreak/role-hijack rules on
    output) and the default action is different (redact vs. block).
    """

    def __init__(
        self,
        detectors: list[Detector],
        *,
        threshold: float = 0.7,
        mode: OutputMode = "redact",
        marker_format: str = "[REDACTED:{rule_id}]",
    ):
        self.detectors = detectors
        self.threshold = threshold
        self.mode = mode
        self.marker_format = marker_format

    def scan(self, text: str) -> OutputScanResult:
        if not text:
            return OutputScanResult(action="allow", score=0.0)

        findings: list[Finding] = []
        for detector in self.detectors:
            findings.extend(detector.scan(text))

        if not findings:
            return OutputScanResult(action="allow", score=0.0)

        score = max(f.confidence for f in findings)
        triggered = score >= self.threshold

        if not triggered:
            # Findings present but none confident enough — surface them for
            # auditing but don't take action.
            return OutputScanResult(action="allow", score=score, findings=findings)

        if self.mode == "block":
            return OutputScanResult(action="block", score=score, findings=findings)

        if self.mode == "log":
            # Pass through unchanged, but the caller will record the event.
            return OutputScanResult(action="allow", score=score, findings=findings)

        # redact mode (default)
        redacted = redact(text, findings, marker_format=self.marker_format)
        return OutputScanResult(
            action="redact",
            score=score,
            findings=findings,
            redacted_text=redacted,
        )


def default_output_scanner(
    *,
    threshold: float = 0.7,
    mode: OutputMode = "redact",
    marker_format: str = "[REDACTED:{rule_id}]",
) -> OutputScanner:
    """Build the canonical output scanner with PII + Secrets detectors.

    Jailbreak / role-hijack / prompt-leak detectors are intentionally
    excluded — those target attacker technique in user inputs, not
    content in model responses.
    """
    return OutputScanner(
        detectors=[PIIDetector(), SecretsDetector()],
        threshold=threshold,
        mode=mode,
        marker_format=marker_format,
    )
