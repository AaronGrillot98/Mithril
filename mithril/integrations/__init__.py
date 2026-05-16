"""Drop-in integrations for popular LLM frameworks.

Each submodule is independent and uses lazy imports for its third-party
dependency, so importing `mithril.integrations` itself never pulls
LangChain / LiteLLM / etc. — you only pay the cost of the framework
you actually use.

Optional install extras:

    pip install mithril-llm[langchain]   # LangChain Runnables + callbacks
    pip install mithril-llm[litellm]     # LiteLLM drop-in completion()
    pip install mithril-llm[fastapi]     # FastAPI middleware + dependency
                                          # (FastAPI is already a core dep,
                                          # so this extra is provided for
                                          # documentation parity)

For unified access:

    pip install mithril-llm[all]
"""

from mithril.integrations._shared import (
    MithrilBlocked,
    build_default_pipeline,
    extract_message_texts,
)

__all__ = ["MithrilBlocked", "build_default_pipeline", "extract_message_texts"]
