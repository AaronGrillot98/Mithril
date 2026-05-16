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
