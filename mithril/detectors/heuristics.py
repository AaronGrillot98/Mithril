"""Rule-based detectors. Fast, deterministic, no external dependencies.

These cover the long tail of well-known jailbreak and injection patterns
documented across academic papers, the OWASP LLM Top 10, and public
jailbreak collections (DAN, AIM, Developer Mode, etc.).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from mithril.detectors.base import Detector
from mithril.models import Finding, Severity


@dataclass(frozen=True)
class Rule:
    rule_id: str
    pattern: re.Pattern[str]
    severity: Severity
    confidence: float
    message: str


def _compile(rules: list[tuple[str, str, Severity, float, str]]) -> tuple[Rule, ...]:
    return tuple(
        Rule(rid, re.compile(pat, re.IGNORECASE | re.DOTALL), sev, conf, msg)
        for rid, pat, sev, conf, msg in rules
    )


class _RuleDetector(Detector):
    # Subclasses MUST override `rules` with their own compiled list. We use a
    # ClassVar-style tuple sentinel as the default so any subclass that forgets
    # gets a clean empty result instead of silently sharing a mutable list.
    rules: tuple[Rule, ...] = ()

    def scan(self, text: str) -> list[Finding]:
        if not text:
            return []
        findings: list[Finding] = []
        for rule in self.rules:
            # Find ALL non-overlapping matches, not just the first — the output
            # redactor needs every span if the same secret pattern occurs more
            # than once in a response.
            for match in rule.pattern.finditer(text):
                excerpt = text[max(0, match.start() - 20) : match.end() + 20].strip()
                findings.append(
                    Finding(
                        detector=self.name,
                        rule_id=rule.rule_id,
                        severity=rule.severity,
                        confidence=rule.confidence,
                        message=rule.message,
                        excerpt=excerpt[:200],
                        start=match.start(),
                        end=match.end(),
                    )
                )
        return findings


class JailbreakDetector(_RuleDetector):
    """Catches well-known jailbreak personas and bypass framings."""

    name = "jailbreak"
    rules = _compile([
        (
            "JB001",
            r"\bDAN\b.{0,40}(do anything now|jailbreak|no restrictions)",
            "high",
            0.95,
            "DAN ('Do Anything Now') jailbreak persona invocation.",
        ),
        (
            "JB002",
            r"\b(developer mode|dev mode)\b.{0,60}(enabled|activated|on)",
            "high",
            0.9,
            "'Developer mode' jailbreak framing.",
        ),
        (
            "JB003",
            r"\bAIM\b.{0,40}(always intelligent|machiavellian)",
            "high",
            0.95,
            "AIM (Always Intelligent and Machiavellian) jailbreak.",
        ),
        (
            "JB004",
            r"\bSTAN\b.{0,60}(strive to avoid norms|no rules)",
            "high",
            0.9,
            "STAN jailbreak persona.",
        ),
        (
            "JB005",
            r"pretend (you are|to be) (an? )?(evil|unrestricted|uncensored|jailbroken)",
            "medium",
            0.75,
            "Role-play framing requesting unrestricted behavior.",
        ),
        (
            "JB006",
            r"(grandma|grandmother).{0,80}(napalm|bomb|virus|exploit|chemical)",
            "high",
            0.9,
            "'Grandma exploit' — disguising harmful requests as bedtime stories.",
        ),
        (
            "JB007",
            r"\b(hypothetically|hypothetical|in a fictional|in a story|imagine you|fictional story)\b.{0,80}(no (rules|restrictions|guidelines|filters)|illegal|harmful)",
            "medium",
            0.7,
            "Hypothetical/fictional framing used to bypass guardrails.",
        ),
        (
            "JB008",
            r"\b(ignore|disregard|forget).{0,30}(previous|prior|above|all).{0,30}(instructions|prompts?|rules|guidelines)",
            "critical",
            0.97,
            "Classic instruction-override (ignore previous instructions).",
        ),
        (
            "JB011",
            # "forget the above and answer freely" / "ignore everything above and ..."
            # Catches the override pattern when the trailing keyword (instructions/
            # rules/etc.) is omitted but intent is clear from context (answer
            # freely / respond freely / and answer / and respond / and tell).
            r"\b(ignore|disregard|forget)\s+(the\s+|everything\s+|all\s+(of\s+)?)?(above|previous|prior)\b.{0,40}\b(answer|respond|reply|tell|comply|do)\b",
            "high",
            0.85,
            "Instruction-override variant without the trailing 'instructions' keyword.",
        ),
        (
            "JB009",
            r"\byou (are|will be) now.{0,40}(unrestricted|uncensored|free|no longer (bound|restricted))",
            "high",
            0.9,
            "Identity-override framing.",
        ),
        (
            "JB010",
            r"\bbypass\b.{0,40}(filter|safety|guardrail|moderation|restriction)",
            "high",
            0.9,
            "Explicit request to bypass safety systems.",
        ),
    ])


class RoleHijackDetector(_RuleDetector):
    """Detects attempts to forge system/assistant turns inside user content."""

    name = "role_hijack"
    rules = _compile([
        (
            "RH001",
            r"<\s*system\s*>",
            "high",
            0.9,
            "Embedded <system> tag — attempt to forge a system message.",
        ),
        (
            "RH002",
            r"<\|\s*(im_start|im_end|system|assistant|user)\s*\|>",
            "critical",
            0.95,
            "ChatML control token injected into user content.",
        ),
        (
            "RH003",
            r"^\s*system\s*:\s*",
            "medium",
            0.7,
            "Line starting with 'system:' — possible role-prefix injection.",
        ),
        (
            "RH004",
            r"\[\s*INST\s*\]|\[\s*/INST\s*\]",
            "high",
            0.9,
            "Llama-style [INST] tokens injected into user content.",
        ),
        (
            "RH005",
            r"###\s*(System|Instruction|Assistant)\s*:",
            "medium",
            0.7,
            "Markdown-style role header injection.",
        ),
    ])


class SystemPromptLeakDetector(_RuleDetector):
    """Catches attempts to extract the system prompt."""

    name = "prompt_leak"
    rules = _compile([
        (
            "PL001",
            r"(repeat|print|show|reveal|output|display).{0,30}(your )?(system )?(prompt|instructions|initial message)",
            "high",
            0.9,
            "Direct request to reveal the system prompt.",
        ),
        (
            "PL002",
            r"what (were|are) your (initial |original )?(instructions|prompts?|rules|guidelines)",
            "medium",
            0.8,
            "Question targeting original system instructions.",
        ),
        (
            "PL003",
            r"begin (your|the) (reply|response) with.{0,20}(system|the text above)",
            "high",
            0.9,
            "Trick to coerce verbatim system-prompt repetition.",
        ),
        (
            "PL004",
            r"translate (the|your) (system )?(prompt|instructions) (into|to)",
            "medium",
            0.75,
            "Translation-based prompt leak attempt.",
        ),
    ])


class PIIDetector(_RuleDetector):
    """Conservative PII detector. Tuned to minimize false positives."""

    name = "pii"
    rules = _compile([
        (
            "PII001",
            r"\b\d{3}-\d{2}-\d{4}\b",
            "high",
            0.95,
            "US Social Security Number format.",
        ),
        (
            "PII002",
            r"\b(?:\d[ -]*?){13,16}\b",
            "medium",
            0.6,
            "Possible credit card number (length 13-16 digits).",
        ),
        (
            "PII003",
            r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{20,}",
            "critical",
            0.98,
            "OpenAI-style API key.",
        ),
        (
            "PII004",
            r"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}(?![A-Za-z0-9])",
            "critical",
            0.99,
            "AWS access key ID.",
        ),
        (
            "PII005",
            r"(?<![A-Za-z0-9])ghp_[A-Za-z0-9]{30,}",
            "critical",
            0.99,
            "GitHub personal access token.",
        ),
        (
            "PII006",
            r"(?<![A-Za-z0-9])xox[abp]-[A-Za-z0-9-]{10,}",
            "critical",
            0.98,
            "Slack token.",
        ),
        (
            "PII007",
            r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
            "critical",
            0.99,
            "Private key material.",
        ),
    ])


class SecretsDetector(_RuleDetector):
    """Generic high-entropy / secret-pattern detector for less-specific leaks."""

    name = "secrets"
    rules = _compile([
        (
            "SEC001",
            r"\b(password|passwd|pwd)\s*[:=]\s*['\"]?[^\s'\"]{6,}",
            "medium",
            0.7,
            "Plaintext password assignment.",
        ),
        (
            "SEC002",
            r"\b(api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*['\"]?[A-Za-z0-9\-_]{16,}",
            "high",
            0.85,
            "Generic API key / token assignment.",
        ),
        (
            "SEC003",
            r"\bBearer\s+[A-Za-z0-9\-_\.=]{20,}",
            "medium",
            0.75,
            "Bearer token in Authorization-style format.",
        ),
    ])
