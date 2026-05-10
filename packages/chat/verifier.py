"""Regex-based literal-numeral verifier — the citation contract teeth.

Any numeral in the rendered text that wasn't injected by the renderer
fails verification. The planner must retry with stricter prompting.

The regex is deliberately broad: it catches integers, decimals, and
Indian-style comma-grouped numbers (e.g. '4,82,310'). It will produce a
false positive if the rendered text legitimately contains a non-metric
numeral (e.g. 'Q3 2026', '5-star review'). The contract trades off some
LLM friction for zero hallucinated numerical claims — that's the whole
point. We accept the friction.
"""

from __future__ import annotations

import re

NUMERAL_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")


class VerifierError(ValueError):
    """Raised when rendered text contains a numeral not produced by render()."""

    def __init__(self, numeral: str, offset: int, context: str, all_violations: list[dict]):
        super().__init__(
            f"Uncited numeral '{numeral}' at offset {offset}. "
            f"Context: '{context}'. Total violations: {len(all_violations)}."
        )
        self.numeral = numeral
        self.offset = offset
        self.context = context
        self.violations = all_violations


def verify_no_uncited_numerals(text: str, substituted_values: frozenset[str] | set[str]) -> None:
    """Raise VerifierError if any literal numeral isn't in substituted_values.

    This is the only place where text from the LLM is admitted to the user.
    """
    violations: list[dict] = []
    for m in NUMERAL_RE.finditer(text):
        n = m.group()
        if n not in substituted_values:
            violations.append(
                {
                    "numeral": n,
                    "offset": m.start(),
                    "context": text[max(0, m.start() - 30) : m.end() + 30],
                }
            )
    if violations:
        first = violations[0]
        raise VerifierError(
            numeral=first["numeral"],
            offset=first["offset"],
            context=first["context"],
            all_violations=violations,
        )


def find_violations(text: str, substituted_values: frozenset[str] | set[str]) -> list[dict]:
    """Non-raising variant — returns a list of violations (empty if clean).

    Useful for the planner's reject-and-retry loop and for evals.
    """
    out: list[dict] = []
    for m in NUMERAL_RE.finditer(text):
        n = m.group()
        if n not in substituted_values:
            out.append(
                {
                    "numeral": n,
                    "offset": m.start(),
                    "context": text[max(0, m.start() - 30) : m.end() + 30],
                }
            )
    return out
