"""OutputScanner — Mithril's response-side firewall."""

from __future__ import annotations

from typing import Literal

from mithril.detectors.base import Detector
from mithril.detectors.heuristics import PIIDetector, SecretsDetector
from mithril.models import Finding, OutputScanResult
from mithril.output.redactor import redact


OutputMode = Literal["block", "redact", "log"]


def _record_metrics(result: OutputScanResult) -> None:
    try:
        from mithril.metrics import record_output_action

        record_output_action(result.action, result)
    except Exception:  # nosec B110 — metrics must never break the scan path  # noqa: BLE001
        pass


def _record_metrics_log(result: OutputScanResult) -> None:
    try:
        from mithril.metrics import record_output_action

        # In log mode the action is "allow" but the scanner still found
        # something; surface it under a synthetic "log" mode label.
        record_output_action("log", result)
    except Exception:  # nosec B110 — metrics must never break the scan path  # noqa: BLE001
        pass


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
            result = OutputScanResult(action="block", score=score, findings=findings)
            _record_metrics(result)
            return result

        if self.mode == "log":
            # Pass through unchanged, but the caller will record the event.
            result = OutputScanResult(action="allow", score=score, findings=findings)
            _record_metrics_log(result)
            return result

        # redact mode (default)
        redacted = redact(text, findings, marker_format=self.marker_format)
        result = OutputScanResult(
            action="redact",
            score=score,
            findings=findings,
            redacted_text=redacted,
        )
        _record_metrics(result)
        return result


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
