from __future__ import annotations

from mithril.judges.base import Judge
from mithril.models import JudgeVerdict


class NoopJudge(Judge):
    """A judge that always returns a low-confidence benign verdict.

    Useful when the judge is configured "off" but call sites still want to
    invoke the same interface.
    """

    name = "noop"

    async def verdict(self, text: str) -> JudgeVerdict:
        return JudgeVerdict(
            verdict="benign",
            confidence=0.0,
            reason="judge disabled",
            model="noop",
        )
