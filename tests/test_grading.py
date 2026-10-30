from __future__ import annotations

import pytest

from strategy_card.grading import WARNING_WEIGHTS, grade
from strategy_card.rules import ERROR, WARNING, Finding


def w(rule_id, flag):
    return Finding(rule_id, flag, WARNING, "x", "m", flag)


def e(rule_id="SC001"):
    return Finding(rule_id, "err", ERROR, "x", "m", None)


ZERO = w("SC101", "zero-costs")  # weight 2
PIT = w("SC102", "pit")  # weight 1
SURV = w("SC103", "survivor")  # weight 1
DSR = w("SC104", "low-dsr")  # weight 2
DECAY = w("SC105", "oos-decay")  # weight 1
SHORT = w("SC106", "short-oos")  # weight 1


def test_no_findings_is_a():
    g = grade([])
    assert g.letter == "A" and g.flags == () and g.points == 0


@pytest.mark.parametrize(
    "findings,letter",
    [
        ([PIT], "B"),
        ([ZERO], "C"),
        ([PIT, SURV], "C"),
        ([ZERO, PIT], "D"),
        ([ZERO, DSR], "D"),
        ([ZERO, DSR, PIT], "E"),
        ([ZERO, DSR, PIT, SURV, DECAY, SHORT], "E"),
    ],
)
def test_warning_points_map_to_letters(findings, letter):
    assert grade(findings).letter == letter


def test_any_error_is_f_regardless_of_warnings():
    assert grade([e()]).letter == "F"
    assert grade([e(), PIT]).letter == "F"


def test_weights_cover_exactly_the_warning_rules():
    from strategy_card.rules import RULES

    warning_ids = {r.id for r in RULES if r.severity == WARNING}
    assert set(WARNING_WEIGHTS) == warning_ids


def test_flags_are_deduplicated_and_in_rule_order():
    g = grade([DSR, ZERO, DSR])
    assert g.flags == ("zero-costs", "low-dsr")
    assert g.points == 4  # duplicates of one rule count once


def test_grade_is_order_independent():
    a = grade([ZERO, PIT, SHORT])
    b = grade([SHORT, ZERO, PIT])
    assert a == b


def test_f_grade_counts_errors():
    g = grade([e("SC001"), e("SC002"), PIT])
    assert g.letter == "F" and g.errors == 2
