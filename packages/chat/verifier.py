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


def find_violations(text: str, substituted_values: frozenset[str] | set[str]) -> list[dict]:
    return [
        {
            "numeral": m.group(),
            "offset": m.start(),
            "context": text[max(0, m.start() - 30) : m.end() + 30],
        }
        for m in NUMERAL_RE.finditer(text)
        if m.group() not in substituted_values
    ]


def verify_no_uncited_numerals(text: str, substituted_values: frozenset[str] | set[str]) -> None:
    violations = find_violations(text, substituted_values)
    if violations:
        first = violations[0]
        raise VerifierError(
            numeral=first["numeral"],
            offset=first["offset"],
            context=first["context"],
            all_violations=violations,
        )
