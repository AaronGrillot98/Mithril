# Security Policy

Mithril is a security tool, so vulnerabilities in Mithril itself deserve special care.

## Supported versions

| Version | Supported          |
| ------- | ------------------ |
| 0.2.x   | :white_check_mark: |
| 0.1.x   | :x:                |
| < 0.1   | :x:                |

Mithril is pre-1.0 software. Breaking changes between minor versions are possible; only the latest minor is supported with security fixes.

## Reporting a vulnerability

**Please do not file a public issue for security reports.**

To disclose a vulnerability privately:

1. Open a [GitHub Security Advisory draft](https://github.com/AaronGrillot98/mithril/security/advisories/new) on this repository, **or**
2. Email the maintainer with subject line `[mithril] security: <short title>` (see the GitHub profile for current contact info).

When reporting, please include:

- A description of the issue and its impact.
- A minimal proof-of-concept (input, expected vs. actual behavior).
- The version / commit of Mithril you tested against.
- Any suggested mitigation, if you have one.

You will receive an acknowledgement within **3 business days**. We will work with you on a fix and a coordinated disclosure timeline (typically 14–90 days depending on severity).

## In-scope

- **Bypasses of the heuristic detection layer** that allow a known attack class (instruction override, role hijack, jailbreak persona, PII / secret exfil) to pass through unflagged.
- **Bypasses of the LLM judge** via second-order injection (e.g., manipulating the judge into emitting a "benign" verdict on an actual attack).
- **Privilege / scope escalation** in the proxy itself — header smuggling, request smuggling, path traversal in dashboard routes, SQL injection in the event log, etc.
- **Resource exhaustion / denial-of-service** in the detection pipeline that can be triggered by user input (catastrophic backtracking, memory blow-up, etc.).

## Out of scope

- **Novel attack patterns not yet in the rule set.** New jailbreak patterns are *features*, not vulnerabilities — please open a normal issue or PR with a new rule and a test case.
- **The upstream LLM saying something harmful.** Mithril is a request gate, not a response moderator. Output-side filtering is on the roadmap (v0.4).
- **False positives on benign prompts**, unless they suggest a fundamentally broken rule. Use issues for false-positive reports.
- **Vulnerabilities in dependencies** that Mithril cannot remediate without an upstream fix. Please report those to the dependency maintainer directly; we will pull in the fix once available.

## Safe harbor

We will not pursue legal action against researchers who:
- Report vulnerabilities through the channels above and in good faith,
- Avoid harm to users and their data,
- Avoid exfiltrating user data beyond what is strictly necessary for the proof-of-concept,
- Give us a reasonable window to fix the issue before public disclosure.
