"""Deterministic A-F grade from validator findings. Documented in docs/RULES.md.

- Any error -> F.
- Otherwise each distinct warning rule adds its weight; the sum maps to a letter:
  0 -> A, 1 -> B, 2 -> C, 3-4 -> D, 5 or more -> E.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .rules import ERROR, RULES, WARNING, Finding

WARNING_WEIGHTS: dict[str, int] = {
    "SC101": 2,  # zero-costs
    "SC102": 1,  # not-point-in-time
    "SC103": 1,  # survivorship-bias
    "SC104": 2,  # low-dsr
    "SC105": 1,  # oos-decay
    "SC106": 1,  # short-oos
}

# (minimum points, letter), checked from the top.
LETTER_BANDS: tuple[tuple[int, str], ...] = ((5, "E"), (3, "D"), (2, "C"), (1, "B"), (0, "A"))

_RULE_ORDER = {r.id: i for i, r in enumerate(RULES)}


@dataclass(frozen=True)
class Grade:
    letter: str
    points: int
    errors: int
    flags: tuple[str, ...]


def grade(findings: Iterable[Finding]) -> Grade:
    findings = list(findings)
    errors = sum(1 for f in findings if f.severity == ERROR)
    warning_ids = sorted(
        {f.id for f in findings if f.severity == WARNING},
        key=lambda i: (_RULE_ORDER.get(i, len(_RULE_ORDER)), i),
    )
    flags_by_id = {f.id: f.flag for f in findings if f.severity == WARNING and f.flag}
    flags = tuple(flags_by_id[i] for i in warning_ids if i in flags_by_id)
    points = sum(WARNING_WEIGHTS.get(i, 1) for i in warning_ids)
    if errors:
        return Grade("F", points, errors, flags)
    letter = next(letter for floor, letter in LETTER_BANDS if points >= floor)
    return Grade(letter, points, errors, flags)
