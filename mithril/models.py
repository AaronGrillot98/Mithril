from typing import Any, Literal

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str
    content: str | list[dict[str, Any]] | None = None

    def text(self) -> str:
        """Flatten message content to a single string for analysis."""
        if self.content is None:
            return ""
        if isinstance(self.content, str):
            return self.content
        parts: list[str] = []
        for chunk in self.content:
            if isinstance(chunk, dict):
                t = chunk.get("text") or chunk.get("content")
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(parts)


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[Message]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None

    model_config = {"extra": "allow"}


Severity = Literal["info", "low", "medium", "high", "critical"]


class Finding(BaseModel):
    detector: str
    rule_id: str
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    message: str
    excerpt: str = ""
    # Character offsets into the scanned text. Used by the output redactor
    # to rewrite the matched span. Default to 0 for backwards compatibility
    # with any externally-constructed Finding (tests, integrations, etc.).
    start: int = 0
    end: int = 0


JudgeVerdictKind = Literal["attack", "benign", "error"]


class JudgeVerdict(BaseModel):
    """A second-opinion classification from the LLM judge."""

    verdict: JudgeVerdictKind
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""
    model: str = ""
    latency_ms: float = 0.0


class DetectionResult(BaseModel):
    blocked: bool
    score: float = Field(ge=0.0, le=1.0)
    findings: list[Finding] = []
    # Set when the judge was consulted (request fell in the ambiguous zone).
    judge: JudgeVerdict | None = None

    @property
    def top_severity(self) -> Severity:
        order: list[Severity] = ["critical", "high", "medium", "low", "info"]
        seen = {f.severity for f in self.findings}
        for s in order:
            if s in seen:
                return s
        return "info"


class BlockResponse(BaseModel):
    error: dict[str, Any]

    @classmethod
    def from_result(cls, result: DetectionResult) -> "BlockResponse":
        body: dict[str, Any] = {
            "type": "mithril_blocked",
            "message": "Request blocked by Mithril.",
            "score": result.score,
            "severity": result.top_severity,
            "findings": [f.model_dump() for f in result.findings],
        }
        if result.judge is not None:
            body["judge"] = result.judge.model_dump()
        return cls(error=body)


OutputAction = Literal["allow", "redact", "block"]


class OutputScanResult(BaseModel):
    """Result of scanning an LLM response for PII / secrets / leaks.

    `redacted_text` is set when at least one finding fired AND the
    configured mode is "redact" — it contains the original text with
    matched spans replaced by `[REDACTED:<rule_id>]` markers.
    """

    action: OutputAction
    score: float = Field(ge=0.0, le=1.0)
    findings: list[Finding] = []
    redacted_text: str | None = None

    @property
    def top_severity(self) -> Severity:
        order: list[Severity] = ["critical", "high", "medium", "low", "info"]
        seen = {f.severity for f in self.findings}
        for s in order:
            if s in seen:
                return s
        return "info"


class OutputBlockResponse(BaseModel):
    """Distinct error envelope when a response (not a request) is blocked.

    Same shape as BlockResponse but with a different `type` so client-side
    error handlers can tell input-blocks from output-blocks at a glance.
    """

    error: dict[str, Any]

    @classmethod
    def from_result(cls, result: OutputScanResult) -> "OutputBlockResponse":
        return cls(
            error={
                "type": "mithril_output_blocked",
                "message": "Response blocked by Mithril output filter.",
                "score": result.score,
                "severity": result.top_severity,
                "findings": [f.model_dump() for f in result.findings],
            }
        )
