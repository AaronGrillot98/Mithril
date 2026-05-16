# Contributing to Mithril

Thanks for caring about LLM security. Mithril is designed to be small, auditable, and easy to extend — most contributions are a single regex, a test case, or a benchmark prompt.

## Quick paths

- **Spotted a new jailbreak / injection pattern in the wild?** Open a [new attack pattern](https://github.com/AaronGrillot98/mithril/issues/new?template=attack-pattern.yml) issue with the prompt and source.
- **A benign prompt that gets flagged?** Open a [false positive](https://github.com/AaronGrillot98/mithril/issues/new?template=false-positive.yml) issue.
- **Want to add a detector rule?** See [Adding a rule](#adding-a-rule) below.
- **Want to add a judge provider?** See [Adding a judge provider](#adding-a-judge-provider).
- **Security vulnerability in Mithril itself?** See [SECURITY.md](SECURITY.md). Do not file a public issue.

## Development setup

```bash
git clone https://github.com/AaronGrillot98/mithril
cd mithril
python -m venv .venv
. .venv/bin/activate           # or .venv\Scripts\Activate.ps1 on Windows
pip install -e ".[dev]"

pytest                          # 32 tests, ~1.5s
python scripts/benchmark.py     # 80-prompt corpus
ruff check .                    # lint
```

## Adding a rule

Each detector rule is a single line in [`mithril/detectors/heuristics.py`](mithril/detectors/heuristics.py):

```python
(
    "JB011",                                 # stable rule ID (don't reuse old ones)
    r"the regex pattern",                    # case-insensitive, dotall by default
    "high",                                  # severity: info|low|medium|high|critical
    0.92,                                    # confidence: 0.0-1.0
    "Short message explaining what fired.", # shown to operators
),
```

Then add a test in [`tests/test_detectors.py`](tests/test_detectors.py) that proves:
1. The intended attack pattern fires the rule.
2. At least one *benign* phrasing using the same vocabulary does **not** fire (no false positives).

Add at least one new entry to [`scripts/benchmark_data.jsonl`](scripts/benchmark_data.jsonl) (one attack, ideally one benign control) so the benchmark covers it.

**Rule ID conventions:**
- `JBnnn` — jailbreak persona / instruction override
- `RHnnn` — role / token hijack
- `PLnnn` — prompt-leak attempts
- `PIInnn` — personally identifiable information patterns
- `SECnnn` — generic secret / credential patterns

## Adding a judge provider

The judge interface is in [`mithril/judges/base.py`](mithril/judges/base.py). To add a provider:

1. Implement `Judge.verdict(text) -> JudgeVerdict` (async, must never raise — return `JudgeVerdict(verdict="error", ...)` on failure).
2. Register it in [`mithril/judges/factory.py`](mithril/judges/factory.py) under a new `judge_provider` literal.
3. Add tests under `tests/test_judge.py` mocking the HTTP layer.

If your provider speaks OpenAI's chat-completions schema, you probably don't need a new class — `OpenAICompatibleJudge` likely already works. Just document the `MITHRIL_JUDGE_BASE_URL` / `MITHRIL_JUDGE_MODEL` values in the README.

## Pull request checklist

- [ ] `pytest` passes locally (32+ tests).
- [ ] `python scripts/benchmark.py` does not regress (still 100% on the existing corpus).
- [ ] `ruff check .` is clean.
- [ ] New behavior has a test.
- [ ] If you added an env var, it's documented in `.env.example` and the README config table.
- [ ] If you changed user-visible behavior, add an entry to `CHANGELOG.md` under `## Unreleased`.

CI runs the same checks across Ubuntu + Windows on Python 3.10/3.11/3.12 — if your PR is green there, you're good.

## Code style

- Python 3.10+ syntax (union types `X | Y`, `from __future__ import annotations` at top of files).
- Functions over classes where possible.
- Public functions: short docstrings explaining the *why* (the *what* is in the name).
- No comments inside regex rule tables; the rule ID is the documentation handle.
- 100-char line limit (enforced by ruff).

## License

By contributing, you agree your contributions are licensed under the same Apache 2.0 license that covers the project.
