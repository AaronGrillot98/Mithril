"""Prometheus metrics for Mithril.

Custom counters and histograms exposed alongside the default HTTP metrics
that ``prometheus-fastapi-instrumentator`` registers on ``/metrics``.

All metrics live on the default ``prometheus_client`` registry so they show
up automatically when the instrumentator exposes ``/metrics``. The module
import is cheap and side-effect-only (just registers metric objects), so
it's safe to import at server startup.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

BLOCKED_TOTAL = Counter(
    "mithril_blocked_total",
    "Requests blocked at the input scan, labeled by severity / rule / detector.",
    ["severity", "rule_id", "detector"],
)

ALLOWED_TOTAL = Counter(
    "mithril_allowed_total",
    "Requests allowed through the input scan.",
)

SCAN_DURATION = Histogram(
    "mithril_scan_duration_seconds",
    "Wall-clock time spent in the detection pipeline (including judge calls).",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

JUDGE_CALLS = Counter(
    "mithril_judge_calls_total",
    "Judge invocations, labeled by verdict (attack / benign / error).",
    ["verdict"],
)

OUTPUT_BLOCKED = Counter(
    "mithril_output_blocked_total",
    "Output scanner actions, labeled by mode and severity.",
    ["mode", "severity"],
)

EVENT_LOG_WRITES = Counter(
    "mithril_event_log_writes_total",
    "Rows inserted into the SQLite event log.",
)


def record_input_result(result: object) -> None:
    """Increment input-scan counters from a ``DetectionResult``.

    Imported lazily where it's used to keep this module dependency-light.
    """
    blocked = getattr(result, "blocked", False)
    findings = getattr(result, "findings", []) or []
    if blocked:
        if findings:
            top = max(findings, key=lambda f: getattr(f, "confidence", 0.0))
            BLOCKED_TOTAL.labels(
                severity=getattr(top, "severity", "unknown"),
                rule_id=getattr(top, "rule_id", "unknown"),
                detector=getattr(top, "detector", "unknown"),
            ).inc()
        else:
            BLOCKED_TOTAL.labels(
                severity="unknown",
                rule_id="unknown",
                detector="unknown",
            ).inc()
    else:
        ALLOWED_TOTAL.inc()


def record_output_action(action: str, result: object) -> None:
    """Increment the output-scan counter from an ``OutputScanResult``."""
    if action == "allow":
        return
    findings = getattr(result, "findings", []) or []
    if findings:
        top = max(findings, key=lambda f: getattr(f, "confidence", 0.0))
        severity = getattr(top, "severity", "unknown")
    else:
        severity = "unknown"
    OUTPUT_BLOCKED.labels(mode=action, severity=severity).inc()
