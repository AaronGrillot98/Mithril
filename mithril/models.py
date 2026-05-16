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


class DetectionResult(BaseModel):
    blocked: bool
    score: float = Field(ge=0.0, le=1.0)
    findings: list[Finding] = []

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
        return cls(
            error={
                "type": "promptguard_blocked",
                "message": "Request blocked by PromptGuard.",
                "score": result.score,
                "severity": result.top_severity,
                "findings": [f.model_dump() for f in result.findings],
            }
        )
