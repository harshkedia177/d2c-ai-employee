"""Regex-based literal-numeral verifier enforcing the citation contract."""

from __future__ import annotations

import re

# Matches integers, decimals, and Indian-style comma-grouped numbers ('4,82,310').
NUMERAL_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")


class VerifierError(ValueError):
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
    """Raise VerifierError if any literal numeral isn't in substituted_values."""
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
    """Non-raising variant — returns a list of violations (empty if clean)."""
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
