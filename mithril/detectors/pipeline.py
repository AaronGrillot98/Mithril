from __future__ import annotations

from mithril.detectors.base import Detector
from mithril.detectors.heuristics import (
    JailbreakDetector,
    PIIDetector,
    RoleHijackDetector,
    SecretsDetector,
    SystemPromptLeakDetector,
)
from mithril.models import DetectionResult, Finding


class DetectionPipeline:
    """Runs a fixed set of detectors and aggregates findings into a single score.

    The aggregation rule is intentionally simple: score = max(confidence over all
    findings). This keeps results explainable — a request blocked at 0.95 is
    blocked because *some specific rule* fired at 0.95, not because of opaque
    weights.
    """

    def __init__(self, detectors: list[Detector], threshold: float = 0.7):
        self.detectors = detectors
        self.threshold = threshold

    def scan(self, text: str) -> DetectionResult:
        findings: list[Finding] = []
        for detector in self.detectors:
            findings.extend(detector.scan(text))

        score = max((f.confidence for f in findings), default=0.0)
        return DetectionResult(
            blocked=score >= self.threshold,
            score=score,
            findings=findings,
        )

    def scan_messages(self, texts: list[str]) -> DetectionResult:
        """Scan multiple message bodies and merge results."""
        combined: list[Finding] = []
        for t in texts:
            combined.extend(self.scan(t).findings)
        score = max((f.confidence for f in combined), default=0.0)
        return DetectionResult(
            blocked=score >= self.threshold,
            score=score,
            findings=combined,
        )


def default_pipeline(threshold: float = 0.7) -> DetectionPipeline:
    return DetectionPipeline(
        detectors=[
            JailbreakDetector(),
            RoleHijackDetector(),
            SystemPromptLeakDetector(),
            PIIDetector(),
            SecretsDetector(),
        ],
        threshold=threshold,
    )
