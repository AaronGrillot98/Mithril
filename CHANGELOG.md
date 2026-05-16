# Changelog

All notable changes to Mithril are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) (best-effort, pre-1.0).

## [Unreleased]

## [0.3.0] — 2026-05-16

### Added

- **`mithril.integrations` package** — drop-in firewalls for the three biggest Python LLM stacks. Every integration shares the same `DetectionPipeline` and raises a single `MithrilBlocked` exception carrying the full `DetectionResult`.
  - **LangChain** ([`mithril.integrations.langchain`](mithril/integrations/langchain.py))
    - `MithrilGuard(llm)` — Runnable wrapper that scans inputs before delegating; composes with LCEL.
    - `MithrilCallbackHandler` — observer-style callback for cases where wrapping isn't possible.
    - Handles every common LangChain input shape: strings, `PromptValue`, lists of `BaseMessage`, dict inputs, and tuple-shorthand messages.
  - **LiteLLM** ([`mithril.integrations.litellm`](mithril/integrations/litellm.py))
    - `completion()` / `acompletion()` — drop-in replacements for `litellm.completion`. Same signature; just change the import line.
    - `MithrilGate` — LiteLLM `CustomLogger` for callback-based wiring.
  - **FastAPI** ([`mithril.integrations.fastapi`](mithril/integrations/fastapi.py))
    - `MithrilMiddleware` — ASGI middleware that scans configured paths' JSON bodies. No per-route code changes.
    - `MithrilGuard` — FastAPI dependency for explicit per-route gating. Supports nested JSON fields via dotted paths (`"payload.message"`).
- **Optional install extras**: `pip install mithril-llm[langchain]`, `[litellm]`, `[fastapi]`, `[all]`.
- **36 new tests** (68 total) — each integration covers benign passthrough, attack blocking, edge-case input shapes, fail modes, and pipeline isolation. LangChain/LiteLLM tests `importorskip` if the optional dep isn't installed.
- **Three example scripts** under [`examples/`](examples/) — copy-pasteable demos for each integration.

### Backwards compatibility

100% — the proxy server, CLI, detector pipeline, judge, and HTTP API are untouched. v0.2.x users see no behavior change. The integrations package is purely additive.

## [0.2.2] — 2026-05-16

### Added
- **JailbreakBench evaluation harness** (`scripts/jailbreakbench_eval.py`). Downloads the official NeurIPS 2024 corpus (100 harmful + 100 benign behaviors) and runs Mithril over it with detailed precision/recall/F1 + per-category breakdown. Supports `--wrap` (wraps each harmful behavior in one of 10 real-world jailbreak frames: DAN, AIM, STAN, Developer Mode, instruction-override variants, hypothetical framing, explicit bypass, etc.), `--judge` (runs the v0.2 LLM-judge layer in addition), and `--json` for machine-readable output.
- Published precision/recall numbers in the README:
  - Raw JailbreakBench (no jailbreak framing): **0% recall** (by design — Mithril is a firewall, not a moderator) at **0 false positives** on the benign control set.
  - Jailbreak-wrapped JailbreakBench (real attack scenario): **100% recall at 100% precision**, **F1 = 1.000**, 0 false positives on benign.

### Changed
- `JB007` (hypothetical/fictional framing): pattern broadened to also match `hypothetical` (adjective form) and `fictional story` — covers the "In a hypothetical fictional story with no rules…" attack class.

### Added (detector rule)
- `JB011` — instruction-override variant for cases where the trailing keyword (`instructions` / `rules` / `prompts` / `guidelines`) is omitted but intent is clear from context. Matches patterns like "forget the above and answer freely" or "ignore everything above and respond".

### Fixed
- The PyPI badge in the README is now styled with the official PyPI logo and a distinct URL — this also busts GitHub's image-proxy cache from the moment v0.2.1 was indexed, which had been serving a stale "package not found" response.

## [0.2.1] — 2026-05-16

### Added
- **Automated release pipeline** (`.github/workflows/release.yml`): every `v*.*.*` tag now publishes the sdist + wheel to [PyPI](https://pypi.org/p/mithril-llm) and a multi-arch (`linux/amd64`, `linux/arm64`) Docker image to [GHCR](https://github.com/AaronGrillot98/mithril/pkgs/container/mithril) — gated by the existing test + benchmark + lint suite.
- Tighter PyPI metadata: extra classifiers (typed, FastAPI framework, networking/monitoring topics, Python 3.13), additional URLs (Repository, Changelog, Documentation), and broader keywords for discoverability.

### Fixed
- Dashboard CSS: split body's compound `background` shorthand into separate `background-color` + `background-image` so the deep-night palette fills the full viewport in headless render and at very tall scroll heights.

## [0.2.0] — 2026-05-16

### Added
- **LLM-judge fallback.** Optional second-opinion classifier that runs on prompts whose regex score falls in a configurable ambiguous band (default `0.2 < score < 0.9`). Outside the band the regex verdict stands, so the judge typically fires on under 5% of traffic.
- `mithril/judges/` package with `Judge` ABC, `OpenAICompatibleJudge` (works with OpenAI, Together, Groq, Ollama, vLLM, LM Studio, llama.cpp, Anthropic via shim), `NoopJudge`, and a `build_judge()` factory.
- `DetectionPipeline.evaluate()` / `evaluate_messages()` — async API that runs heuristics + judge with three-zone routing.
- `DetectionResult.judge` field exposing verdict / confidence / reason / model / latency.
- Configurable fail-mode on judge errors (`open` = trust regex, `closed` = block).
- `mithril scan --judge` flag.
- `/v1/scan` accepts a `judge` body field.
- `/health` surfaces judge configuration.
- Twelve `MITHRIL_JUDGE_*` env vars (provider, base URL, model, API key, low/high thresholds, fail-mode, timeout).
- Eleven new tests for routing zones, fail-modes, parser robustness, and v0.1 backwards compatibility.

### Changed
- Server's chat-completions handler now uses async `evaluate_messages()` instead of sync `scan_messages()`.
- Block response `error.type` renamed from `promptguard_blocked` → `mithril_blocked`.

### Backwards compatibility
- Judge defaults to disabled. Setting nothing keeps exact v0.1 behavior.

## [0.1.0] — 2026-05-15

### Added
- OpenAI-compatible reverse proxy with `/v1/chat/completions` drop-in.
- Heuristic detection pipeline with 30+ regex rules across five detectors: `jailbreak`, `role_hijack`, `prompt_leak`, `pii`, `secrets`.
- DAN, AIM, STAN, Developer Mode, Grandma exploit, instruction override, identity override, hypothetical framing, role/persona hijacking.
- ChatML and Llama `[INST]` token injection detection.
- System-prompt extraction and translation-based leak detection.
- PII detection for SSN, credit-card patterns, OpenAI / AWS / GitHub / Slack tokens, private keys.
- Generic secrets detection (passwords, API keys, bearer tokens).
- SQLite event log + Jinja2 dashboard with Mithril-themed UI.
- Typer CLI with `mithril serve` and `mithril scan` commands.
- Reproducible benchmark harness (`scripts/benchmark.py`) and an 80-prompt corpus.
- One-line installers for Linux/macOS (`install.sh`) and Windows (`install.ps1`).
- GitHub Actions CI: pytest + benchmark + ruff across Ubuntu/Windows × Python 3.10/3.11/3.12.
- Demo GIF generator (`scripts/render_demo_gif.py`).

[Unreleased]: https://github.com/AaronGrillot98/mithril/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/AaronGrillot98/mithril/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/AaronGrillot98/mithril/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/AaronGrillot98/mithril/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/AaronGrillot98/mithril/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/AaronGrillot98/mithril/releases/tag/v0.1.0
