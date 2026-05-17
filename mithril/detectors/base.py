from __future__ import annotations

from abc import ABC, abstractmethod

from mithril.models import Finding


class Detector(ABC):
    """A single-purpose check that runs against user-provided text.

    Detectors are stateless and may be invoked concurrently across many requests.
    """

    name: str = "detector"

    @abstractmethod
    def scan(self, text: str) -> list[Finding]:
        """Return zero or more findings for the given text."""
        raise NotImplementedError

    def warmup(self) -> None:  # noqa: B027 — intentional default no-op
        """Optional one-time initialization (e.g. loading a model, parsing
        a corpus). Called by the server at startup so the first request
        doesn't pay the cost. Sync — the server invokes it via
        ``asyncio.to_thread`` if it's expensive. Default implementation
        is a no-op; cheap regex-based detectors don't need to override."""
        return None
