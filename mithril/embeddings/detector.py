"""EmbeddingSimilarityDetector — semantic match against canonical jailbreaks."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from mithril.detectors.base import Detector
from mithril.models import Finding

logger = logging.getLogger("mithril.embeddings")


DEFAULT_CORPUS_PATH = Path(__file__).parent / "corpus.jsonl"
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class _Encoder(Protocol):
    """Minimum surface we use from sentence-transformers SentenceTransformer.

    Declared as a Protocol so tests can substitute a deterministic fake
    encoder without pulling in the real (heavy) dependency.
    """

    def encode(self, texts: list[str], normalize_embeddings: bool = ...) -> Any: ...


@dataclass(frozen=True)
class CorpusEntry:
    name: str
    text: str
    category: str = "jailbreak"


def _load_corpus(path: Path) -> list[CorpusEntry]:
    entries: list[CorpusEntry] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            obj = json.loads(line)
            entries.append(
                CorpusEntry(
                    name=str(obj["name"]),
                    text=str(obj["text"]),
                    category=str(obj.get("category", "jailbreak")),
                )
            )
    return entries


class EmbeddingSimilarityDetector(Detector):
    """Detect prompts semantically similar to known jailbreak attacks.

    Args:
        corpus_path: JSONL with one ``{"name": ..., "text": ...}`` entry per
            line. Defaults to the bundled curated corpus.
        model_name: A sentence-transformers model name. The default
            (``all-MiniLM-L6-v2``) is small and fast and works for English
            jailbreaks; swap for a multilingual model if your traffic is
            non-English.
        threshold: Cosine similarity above which a prompt is considered a
            semantic match to a corpus entry. Default 0.80 — tuned to
            minimize false positives on the JailbreakBench benign set.
        confidence_floor: Confidence reported by the detector at exactly
            ``threshold``. Above-threshold matches scale linearly from
            this floor up to 1.0 at perfect similarity (1.0).
        encoder: Optional injected encoder. Used by tests to substitute a
            deterministic fake. In production, leave as None and the
            real sentence-transformers model is loaded lazily.
    """

    name = "embedding"

    def __init__(
        self,
        corpus_path: Path | str | None = None,
        *,
        model_name: str = DEFAULT_MODEL,
        threshold: float = 0.80,
        confidence_floor: float = 0.7,
        encoder: _Encoder | None = None,
    ):
        self.corpus_path = Path(corpus_path) if corpus_path else DEFAULT_CORPUS_PATH
        self.model_name = model_name
        self.threshold = threshold
        self.confidence_floor = confidence_floor

        self._encoder: _Encoder | None = encoder
        self._corpus: list[CorpusEntry] | None = None
        # Held as Any so we don't import numpy at module load.
        self._corpus_embeddings: Any = None
        # If the caller passed an encoder, we can pre-warm. Otherwise we
        # defer to first call so import time stays cheap.
        if encoder is not None:
            self._ensure_loaded()

    def _ensure_loaded(self) -> None:
        if self._encoder is not None and self._corpus_embeddings is not None:
            return

        if self._encoder is None:
            try:
                from sentence_transformers import SentenceTransformer  # type: ignore
            except ImportError as exc:  # pragma: no cover - exercised via tests
                raise ImportError(
                    "EmbeddingSimilarityDetector needs `sentence-transformers`. "
                    "Install with: pip install mithril-llm[embeddings]"
                ) from exc
            logger.info("Loading embedding model %s", self.model_name)
            self._encoder = SentenceTransformer(self.model_name)  # type: ignore[assignment]

        if self._corpus is None:
            self._corpus = _load_corpus(self.corpus_path)

        if not self._corpus:
            logger.warning("Embedding corpus at %s is empty", self.corpus_path)
            self._corpus_embeddings = []
            return

        texts = [c.text for c in self._corpus]
        embeddings = self._encoder.encode(texts, normalize_embeddings=True)
        self._corpus_embeddings = embeddings

    def scan(self, text: str) -> list[Finding]:
        if not text or not text.strip():
            return []

        try:
            self._ensure_loaded()
        except ImportError as exc:
            # Graceful: warn once per process, then act as a no-op detector.
            logger.warning("Embedding detector disabled: %s", exc)
            self._encoder = _NullEncoder()
            return []

        if self._corpus_embeddings is None or len(self._corpus_embeddings) == 0:
            return []

        assert self._encoder is not None
        prompt_emb = self._encoder.encode([text], normalize_embeddings=True)[0]
        sims = self._cosine_similarities(prompt_emb, self._corpus_embeddings)

        # max() of an empty sequence would explode — we guarded above with
        # len(self._corpus_embeddings) == 0, but be defensive.
        if len(sims) == 0:
            return []
        max_idx = int(_argmax(sims))
        max_sim = float(sims[max_idx])
        if max_sim < self.threshold:
            return []

        # Linear scale: threshold → confidence_floor, 1.0 → 1.0.
        confidence = self.confidence_floor + (
            (max_sim - self.threshold) * (1.0 - self.confidence_floor)
            / max(1e-9, 1.0 - self.threshold)
        )
        confidence = max(0.0, min(1.0, confidence))

        assert self._corpus is not None
        match = self._corpus[max_idx]
        return [
            Finding(
                detector=self.name,
                rule_id="EMB001",
                severity="medium",
                confidence=confidence,
                message=(
                    f"Semantically similar to known jailbreak "
                    f"'{match.name}' (cosine={max_sim:.3f})."
                ),
                excerpt=text[:200],
                start=0,
                end=len(text),
            )
        ]

    # ----- helpers -----------------------------------------------------------

    @staticmethod
    def _cosine_similarities(prompt_emb: Any, corpus_embs: Any) -> Any:
        """Compute cosine similarity between a prompt embedding and corpus.

        Both inputs are expected to be unit-normalized (sentence-transformers
        does this when ``normalize_embeddings=True``). With unit vectors,
        cosine similarity = dot product.

        Uses numpy if available, else falls back to a pure-python loop.
        """
        try:
            import numpy as np  # type: ignore

            return np.asarray(corpus_embs) @ np.asarray(prompt_emb)
        except ImportError:  # pragma: no cover
            sims = []
            for row in corpus_embs:
                sims.append(sum(a * b for a, b in zip(row, prompt_emb)))
            return sims


def _argmax(values: Any) -> int:
    """argmax with numpy if available, else pure python."""
    try:
        import numpy as np  # type: ignore

        return int(np.argmax(values))
    except ImportError:  # pragma: no cover
        best_idx = 0
        best_val = values[0]
        for i, v in enumerate(values):
            if v > best_val:
                best_idx = i
                best_val = v
        return best_idx


class _NullEncoder:
    """Used after we've discovered the optional dependency isn't installed
    — every subsequent scan() call short-circuits cheaply."""

    def encode(self, texts: list[str], normalize_embeddings: bool = True) -> list[list[float]]:
        return [[0.0]] * len(texts)
