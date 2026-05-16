# Changelog

All notable changes to Mithril are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) (best-effort, pre-1.0).

## [Unreleased]

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

[Unreleased]: https://github.com/AaronGrillot98/mithril/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/AaronGrillot98/mithril/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/AaronGrillot98/mithril/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/AaronGrillot98/mithril/releases/tag/v0.1.0
