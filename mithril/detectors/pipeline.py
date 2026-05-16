from __future__ import annotations

from typing import Literal

from mithril.detectors.base import Detector
from mithril.detectors.heuristics import (
    JailbreakDetector,
    PIIDetector,
    RoleHijackDetector,
    SecretsDetector,
    SystemPromptLeakDetector,
)
from mithril.judges.base import Judge
from mithril.judges.noop import NoopJudge
from mithril.models import DetectionResult, Finding, JudgeVerdict


FailMode = Literal["open", "closed"]


class DetectionPipeline:
    """Runs detectors and (optionally) an LLM judge for ambiguous prompts.

    Two-stage architecture:

    1. **Heuristic stage** (`scan`) — pure regex rules. Sub-millisecond.
       Produces a regex score = max(confidence) of all firing rules.

    2. **Judge stage** (`evaluate`) — optional LLM second-opinion. Invoked
       only when the regex score falls in the *ambiguous zone* defined by
       `judge_low` < score < `judge_high`. Outside that band the regex
       verdict stands.

    Aggregation: score = max(confidence over all findings). This keeps
    results explainable — a request blocked at 0.95 is blocked because
    *some specific rule* fired at 0.95, not because of opaque weights.
    """

    def __init__(
        self,
        detectors: list[Detector],
        *,
        threshold: float = 0.7,
        judge: Judge | None = None,
        judge_low: float = 0.2,
        judge_high: float = 0.9,
        fail_mode: FailMode = "open",
    ):
        self.detectors = detectors
        self.threshold = threshold
        self.judge = judge or NoopJudge()
        self.judge_low = judge_low
        self.judge_high = judge_high
        self.fail_mode = fail_mode

    # ------------------------------------------------------------------
    # Heuristic stage (sync, no I/O)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Judge stage (async, may do network I/O)
    # ------------------------------------------------------------------

    def _is_judge_enabled(self) -> bool:
        return not isinstance(self.judge, NoopJudge)

    def _in_ambiguous_zone(self, score: float) -> bool:
        return self.judge_low < score < self.judge_high

    async def evaluate(self, text: str) -> DetectionResult:
        """Heuristic + judge. Async because the judge may call out over HTTP."""
        result = self.scan(text)
        return await self._maybe_judge(text, result)

    async def evaluate_messages(self, texts: list[str]) -> DetectionResult:
        result = self.scan_messages(texts)
        # Concatenate texts for the judge so it sees the whole conversation.
        # We deliberately do not run the judge once per message — most
        # multi-turn requests want a single verdict.
        joined = "\n\n".join(t for t in texts if t)
        return await self._maybe_judge(joined, result)

    async def _maybe_judge(self, text: str, regex_result: DetectionResult) -> DetectionResult:
        """Run the judge if the result is in the ambiguous zone, then merge."""
        if not self._is_judge_enabled():
            return regex_result

        if not self._in_ambiguous_zone(regex_result.score):
            # Regex is confident — block or allow, no judge.
            return regex_result

        verdict: JudgeVerdict = await self.judge.verdict(text)
        return self._merge(regex_result, verdict)

    def _merge(self, regex_result: DetectionResult, verdict: JudgeVerdict) -> DetectionResult:
        """Combine regex findings with the judge verdict into a final result."""
        # Handle judge errors per fail-mode policy.
        if verdict.verdict == "error":
            if self.fail_mode == "closed":
                # Fail-closed: treat the error as a confident attack.
                return DetectionResult(
                    blocked=True,
                    score=max(regex_result.score, 0.95),
                    findings=regex_result.findings,
                    judge=verdict,
                )
            # Fail-open: trust the regex result.
            return DetectionResult(
                blocked=regex_result.blocked,
                score=regex_result.score,
                findings=regex_result.findings,
                judge=verdict,
            )

        if verdict.verdict == "attack":
            # Judge says attack — lift the score to the judge's confidence
            # (or keep the regex score, whichever is higher) and block.
            final_score = max(regex_result.score, verdict.confidence)
            return DetectionResult(
                blocked=True,
                score=final_score,
                findings=regex_result.findings,
                judge=verdict,
            )

        # Judge says benign — allow, even though regex found something.
        # We keep the regex score for visibility but unset blocked.
        return DetectionResult(
            blocked=False,
            score=regex_result.score,
            findings=regex_result.findings,
            judge=verdict,
        )

    async def aclose(self) -> None:
        await self.judge.aclose()


def default_pipeline(threshold: float = 0.7, *, judge: Judge | None = None) -> DetectionPipeline:
    return DetectionPipeline(
        detectors=[
            JailbreakDetector(),
            RoleHijackDetector(),
            SystemPromptLeakDetector(),
            PIIDetector(),
            SecretsDetector(),
        ],
        threshold=threshold,
        judge=judge,
    )
