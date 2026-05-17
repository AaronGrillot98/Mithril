"""Embedding-based similarity detection — Mithril's third defense layer.

Where the regex pipeline catches *known attack vocabulary* (DAN, AIM,
instruction override, etc.) and the LLM judge catches *ambiguous
intent*, this layer catches *semantic similarity to known jailbreaks*.
A prompt that doesn't trip any regex but is semantically very close to
a canonical DAN-style attack will still get flagged.

Requires the optional ``[embeddings]`` extra:

    pip install "mithril-llm[embeddings]"

Off by default. Enable with:

    MITHRIL_EMBEDDING_ENABLED=true

The default model (``sentence-transformers/all-MiniLM-L6-v2``) is
~90 MB; it loads once at proxy startup, encoding the bundled corpus
of canonical jailbreak prompts. Per-request scan time is typically
5–30 ms depending on the host.
"""

from mithril.embeddings.detector import EmbeddingSimilarityDetector

__all__ = ["EmbeddingSimilarityDetector"]
