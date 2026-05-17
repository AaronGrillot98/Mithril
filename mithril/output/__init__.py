"""Output scanning — Mithril's response-side firewall.

Where the regular ``DetectionPipeline`` scans *user inputs* for attack
techniques (jailbreaks, role hijacks, instruction overrides), the
``OutputScanner`` here scans *LLM responses* for sensitive material
the model shouldn't be emitting back to the user: PII, credentials,
private keys, etc.

The two pipelines are deliberately separate: input scanning targets
attacker *technique*, output scanning targets *content*. They use
different detector subsets and have different default behaviors
(input defaults to ``block``, output defaults to ``redact`` — because
breaking a response mid-stream is worse UX than silently scrubbing a
leaked SSN).

Public API:

    from mithril.output import OutputScanner, redact

    scanner = OutputScanner.default()
    result = scanner.scan("My SSN is 123-45-6789")
    # result.action == "redact"
    # result.redacted_text == "My SSN is [REDACTED:PII001]"
"""

from mithril.output.redactor import redact
from mithril.output.scanner import OutputScanner, default_output_scanner
from mithril.output.streaming import IncrementalStreamScanner

__all__ = [
    "IncrementalStreamScanner",
    "OutputScanner",
    "default_output_scanner",
    "redact",
]
