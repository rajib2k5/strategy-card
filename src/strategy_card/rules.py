"""Logic rules for a strategy card.

Each rule is a small pure function ``card -> list[Finding]``. Rules assume the card already
passed the JSON Schema and contains no placeholders (the validator guarantees this), so they
may index required keys directly. Every rule is listed in ``RULES`` and documented in
``docs/RULES.md``. Rule IDs are stable: never renumber or reuse one.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from statistics import StatisticsError
from typing import Any

from . import stats

ERROR = "error"
WARNING = "warning"

PLACEHOLDER = "__FILL_ME__"

DSR_THRESHOLD = 0.95
NEGLIGIBLE_COST_BPS = 0.5  # fees + slippage below this total is treated as no cost
# Tolerances cover rounding a stated value to 4 decimal places (max error 5e-5), no more.
SR_TOLERANCE = 1e-4  # annualized Sharpe: stated SR* vs recomputed; DSR SR vs in-sample SR
DSR_TOLERANCE = 1e-4  # probability: stated DSR vs DSR recomputed from the stated inputs
NOBS_FACTOR = 1.2  # (n_observations - 1) vs in-sample periods spanned
NOBS_SLACK_PERIODS = 1.0  # plus or minus one period, for partial periods at the ends
DAYS_PER_YEAR = 365.25
MIN_TRIAL_VARIANCE = 1e-12  # annualized; below this, n_trials > 1 is not credible
OOS_DECAY_RATIO = 0.5
MIN_OOS_DAYS = 365


@dataclass(frozen=True)
class Finding:
    id: str
    name: str
    severity: str
    path: str
    message: str
    flag: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "severity": self.severity,
            "path": self.path,
            "message": self.message,
            "flag": self.flag,
        }


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    severity: str
    flag: str | None
    check: Callable[[dict], list[Finding]]

    def finding(self, path: str, message: str) -> Finding:
        return Finding(self.id, self.name, self.severity, path, message, self.flag)


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _walk(node: Any, path: tuple = ()) -> Iterator[tuple[tuple, Any]]:
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(v, (*path, str(k)))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, (*path, str(i)))
    else:
        yield path, node


# ---------------------------------------------------------------- errors
def sc001_window_order(card: dict) -> list[Finding]:
    out = []
    for name in ("in_sample", "out_of_sample"):
        w = card["test_window"][name]
        if not _d(w["start"]) < _d(w["end"]):
            out.append(
                SC001.finding(
                    f"test_window.{name}",
                    f"{name} start {w['start']} must be before its end {w['end']}",
                )
            )
    return out


def sc002_oos_after_is(card: dict) -> list[Finding]:
    tw = card["test_window"]
    is_end, oos_start = tw["in_sample"]["end"], tw["out_of_sample"]["start"]
    if _d(oos_start) > _d(is_end):
        return []
    return [
        SC002.finding(
            "test_window.out_of_sample.start",
            f"out-of-sample start {oos_start} must be after in-sample end {is_end}",
        )
    ]


def sc003_dsr_required(card: dict) -> list[Finding]:
    count = card["trials"]["count"]
    if count > 1 and "deflated_sharpe" not in card["performance"]:
        return [
            SC003.finding(
                "performance.deflated_sharpe",
                f"trials.count is {count}; a deflated Sharpe ratio is required when more "
                "than one variant was tried",
            )
        ]
    return []


def sc004_dsr_trials_match(card: dict) -> list[Finding]:
    dsr = card["performance"].get("deflated_sharpe")
    if dsr is None:
        return []
    count = card["trials"]["count"]
    if dsr["n_trials"] != count:
        return [
            SC004.finding(
                "performance.deflated_sharpe.n_trials",
                f"deflated_sharpe.n_trials is {dsr['n_trials']} but trials.count is {count}",
            )
        ]
    return []


def sc005_finite_numbers(card: dict) -> list[Finding]:
    out = []
    for path, value in _walk(card):
        if isinstance(value, float) and not math.isfinite(value):
            out.append(SC005.finding(".".join(path), f"value {value} is not a finite number"))
    return out


def _dsr_inputs(card: dict) -> dict | None:
    """The DSR block, or None when absent or when any number is non-finite (SC005's job)."""
    perf = card["performance"]
    dsr = perf.get("deflated_sharpe")
    if dsr is None:
        return None
    numbers = [perf["periods_per_year"], perf["sharpe"]["in_sample"]]
    numbers += [v for v in dsr.values() if isinstance(v, (int, float))]
    if not all(math.isfinite(v) for v in numbers):
        return None
    return dsr


def _recomputed_sr_star_pp(card: dict, dsr: dict) -> float:
    ppy = card["performance"]["periods_per_year"]
    var_pp = dsr["trial_sharpe_variance_annualized"] / ppy
    return stats.expected_max_sharpe(dsr["n_trials"], var_pp)


def sc007_dsr_benchmark(card: dict) -> list[Finding]:
    dsr = _dsr_inputs(card)
    if dsr is None:
        return []
    ppy = card["performance"]["periods_per_year"]
    expected = stats.annualize(_recomputed_sr_star_pp(card, dsr), ppy)
    stated = dsr["sr_benchmark_annualized"]
    if abs(stated - expected) > SR_TOLERANCE:
        return [
            SC007.finding(
                "performance.deflated_sharpe.sr_benchmark_annualized",
                f"stated SR* {stated} but n_trials {dsr['n_trials']} and trial variance "
                f"{dsr['trial_sharpe_variance_annualized']} give {expected:.4f} "
                f"(tolerance {SR_TOLERANCE})",
            )
        ]
    return []


def recomputed_dsr(card: dict, dsr: dict) -> float:
    """DSR from the card's own inputs and the RECOMPUTED SR* (never the stated SR*)."""
    ppy = card["performance"]["periods_per_year"]
    return stats.probabilistic_sharpe_ratio(
        stats.deannualize(dsr["sharpe_annualized"], ppy),
        _recomputed_sr_star_pp(card, dsr),
        dsr["n_observations"],
        dsr["skew"],
        dsr["kurtosis_pearson"],
    )


def sc008_dsr_value(card: dict) -> list[Finding]:
    dsr = _dsr_inputs(card)
    if dsr is None:
        return []
    path = "performance.deflated_sharpe.value"
    try:
        expected = recomputed_dsr(card, dsr)
    except ValueError as exc:
        return [SC008.finding(path, f"the DSR inputs are inconsistent: {exc}")]
    if abs(dsr["value"] - expected) > DSR_TOLERANCE:
        return [
            SC008.finding(
                path,
                f"stated DSR {dsr['value']} but its inputs give {expected:.4f} "
                f"(tolerance {DSR_TOLERANCE})",
            )
        ]
    return []


def sc009_dsr_sharpe_matches(card: dict) -> list[Finding]:
    dsr = _dsr_inputs(card)
    if dsr is None:
        return []
    tested, in_sample = dsr["sharpe_annualized"], card["performance"]["sharpe"]["in_sample"]
    if abs(tested - in_sample) > SR_TOLERANCE:
        return [
            SC009.finding(
                "performance.deflated_sharpe.sharpe_annualized",
                f"DSR tests Sharpe {tested} but the in-sample Sharpe is {in_sample} "
                f"(tolerance {SR_TOLERANCE})",
            )
        ]
    return []


def sc010_dsr_observations(card: dict) -> list[Finding]:
    """n rows span n - 1 periods. Compare n - 1 with the periods in the in-sample window."""
    dsr = _dsr_inputs(card)
    if dsr is None:
        return []
    w = card["test_window"]["in_sample"]
    days = (_d(w["end"]) - _d(w["start"])).days
    if days <= 0:
        return []  # SC001
    ppy = card["performance"]["periods_per_year"]
    periods = days / DAYS_PER_YEAR * ppy
    intervals = dsr["n_observations"] - 1
    lo = periods / NOBS_FACTOR - NOBS_SLACK_PERIODS
    hi = periods * NOBS_FACTOR + NOBS_SLACK_PERIODS
    if not (lo <= intervals <= hi):
        return [
            SC010.finding(
                "performance.deflated_sharpe.n_observations",
                f"{dsr['n_observations']} observations, but the in-sample window of {days} days "
                f"at {ppy} periods per year spans about {periods:.1f} periods "
                f"(allowed: {lo + 1:.0f} to {hi + 1:.0f} observations)",
            )
        ]
    return []


def sc011_dsr_moments(card: dict) -> list[Finding]:
    """Skew and Pearson kurtosis must be possible for a sample of n_observations points.

    Bounds for the moment (bias = True) estimators of n points:
      kurtosis >= skew^2 + 1                      (Pearson's inequality)
      |skew|   <= (n - 2) / sqrt(n - 1)
      kurtosis <= (n^2 - 3n + 3) / (n - 1)
    """
    dsr = _dsr_inputs(card)
    if dsr is None:
        return []
    g3, g4, n = dsr["skew"], dsr["kurtosis_pearson"], dsr["n_observations"]
    path = "performance.deflated_sharpe"
    problems = []
    if g4 < g3 * g3 + 1 - 1e-9:
        problems.append(f"kurtosis {g4} is below skew^2 + 1 = {g3 * g3 + 1:.6g}")
    max_skew = (n - 2) / math.sqrt(n - 1)
    if abs(g3) > max_skew + 1e-9:
        problems.append(f"|skew| {abs(g3)} exceeds {max_skew:.4f}, the maximum for {n} points")
    max_kurt = (n * n - 3 * n + 3) / (n - 1)
    if g4 > max_kurt + 1e-9:
        problems.append(f"kurtosis {g4} exceeds {max_kurt:.4f}, the maximum for {n} points")
    return [SC011.finding(path, "; ".join(problems))] if problems else []


def sc012_trial_variance(card: dict) -> list[Finding]:
    dsr = _dsr_inputs(card)
    if dsr is None or dsr["n_trials"] <= 1:
        return []
    var = dsr["trial_sharpe_variance_annualized"]
    if var < MIN_TRIAL_VARIANCE:
        return [
            SC012.finding(
                "performance.deflated_sharpe.trial_sharpe_variance_annualized",
                f"{dsr['n_trials']} trials with Sharpe variance {var} (below "
                f"{MIN_TRIAL_VARIANCE}): all trials have the same Sharpe ratio, so they count as "
                "one distinct trial and SR* is 0. Check that the variants really differ.",
            )
        ]
    return []


def find_placeholders(card: Any) -> list[Finding]:
    """SC006: every value that still holds the filler placeholder.

    Runs on the raw document *before* the schema, because a placeholder in a numeric or
    enum field is also a schema error and would otherwise be reported only as SC000.
    """
    return [
        SC006.finding(".".join(path), f"placeholder {PLACEHOLDER} must be replaced by a human")
        for path, value in _walk(card)
        if isinstance(value, str) and PLACEHOLDER in value
    ]


# ---------------------------------------------------------------- warnings
def sc101_zero_costs(card: dict) -> list[Finding]:
    c = card["costs"]
    total = c["fees_bps"] + c["slippage_bps"]
    if total < NEGLIGIBLE_COST_BPS:
        return [
            SC101.finding(
                "costs",
                f"fees + slippage = {total} bps, below the {NEGLIGIBLE_COST_BPS} bps "
                "negligible-cost threshold",
            )
        ]
    return []


def sc102_point_in_time(card: dict) -> list[Finding]:
    pit = card["data"]["point_in_time"]
    if pit in ("restated", "unknown", "mixed"):
        return [
            SC102.finding(
                "data.point_in_time",
                f"point_in_time is {pit!r}; restated data can leak future information",
            )
        ]
    return []


def sc103_survivorship(card: dict) -> list[Finding]:
    handling = card["data"]["survivorship"]["handling"]
    if handling in ("survivor_only", "unknown"):
        return [
            SC103.finding(
                "data.survivorship.handling",
                f"survivorship handling is {handling!r}; results can be biased upward",
            )
        ]
    return []


def sc104_low_dsr(card: dict) -> list[Finding]:
    """Grade the LOWER of the stated DSR and the DSR recomputed from the card's inputs, so a
    stated value inside the SC008 tolerance cannot lift the card over the threshold."""
    dsr = card["performance"].get("deflated_sharpe")
    if dsr is None:
        return []
    value = dsr["value"]
    inputs = _dsr_inputs(card)
    if inputs is not None:
        try:
            value = min(value, recomputed_dsr(card, inputs))
        except (KeyError, ValueError, ArithmeticError):
            pass  # SC008 reports unusable inputs; grade the stated value here
    if value < DSR_THRESHOLD:
        return [
            SC104.finding(
                "performance.deflated_sharpe.value",
                f"deflated Sharpe ratio {value:.4f} (lower of stated {dsr['value']} and "
                f"recomputed) is below {DSR_THRESHOLD}",
            )
        ]
    return []


def sc105_oos_decay(card: dict) -> list[Finding]:
    s = card["performance"]["sharpe"]
    if s["in_sample"] > 0 and s["out_of_sample"] < OOS_DECAY_RATIO * s["in_sample"]:
        return [
            SC105.finding(
                "performance.sharpe.out_of_sample",
                f"out-of-sample Sharpe {s['out_of_sample']} is less than "
                f"{OOS_DECAY_RATIO} x in-sample Sharpe {s['in_sample']}",
            )
        ]
    return []


def sc106_short_oos(card: dict) -> list[Finding]:
    w = card["test_window"]["out_of_sample"]
    days = (_d(w["end"]) - _d(w["start"])).days
    if 0 < days < MIN_OOS_DAYS:
        return [
            SC106.finding(
                "test_window.out_of_sample",
                f"out-of-sample window is {days} days, shorter than {MIN_OOS_DAYS}",
            )
        ]
    return []


SC001 = Rule("SC001", "window-order", ERROR, None, sc001_window_order)
SC002 = Rule("SC002", "oos-after-is", ERROR, None, sc002_oos_after_is)
SC003 = Rule("SC003", "dsr-required", ERROR, None, sc003_dsr_required)
SC004 = Rule("SC004", "dsr-trials-mismatch", ERROR, None, sc004_dsr_trials_match)
SC005 = Rule("SC005", "non-finite-number", ERROR, None, sc005_finite_numbers)
SC006 = Rule("SC006", "unfilled-placeholder", ERROR, None, find_placeholders)
SC007 = Rule("SC007", "dsr-benchmark-mismatch", ERROR, None, sc007_dsr_benchmark)
SC008 = Rule("SC008", "dsr-value-mismatch", ERROR, None, sc008_dsr_value)
SC009 = Rule("SC009", "dsr-sharpe-mismatch", ERROR, None, sc009_dsr_sharpe_matches)
SC010 = Rule("SC010", "dsr-observations-implausible", ERROR, None, sc010_dsr_observations)
SC011 = Rule("SC011", "dsr-moments-infeasible", ERROR, None, sc011_dsr_moments)
SC012 = Rule("SC012", "trials-identical", WARNING, "same-trials", sc012_trial_variance)
SC101 = Rule("SC101", "zero-costs", WARNING, "zero-costs", sc101_zero_costs)
SC102 = Rule("SC102", "not-point-in-time", WARNING, "not-pit", sc102_point_in_time)
SC103 = Rule("SC103", "survivorship-bias", WARNING, "survivor-bias", sc103_survivorship)
SC104 = Rule("SC104", "low-dsr", WARNING, "low-dsr", sc104_low_dsr)
SC105 = Rule("SC105", "oos-decay", WARNING, "oos-decay", sc105_oos_decay)
SC106 = Rule("SC106", "short-oos", WARNING, "short-oos", sc106_short_oos)

# SC006 is listed for documentation and grading; the validator runs it before the schema.
RULES: tuple[Rule, ...] = (
    SC001,
    SC002,
    SC003,
    SC004,
    SC005,
    SC006,
    SC007,
    SC008,
    SC009,
    SC010,
    SC011,
    SC012,
    SC101,
    SC102,
    SC103,
    SC104,
    SC105,
    SC106,
)
LOGIC_RULES: tuple[Rule, ...] = tuple(r for r in RULES if r is not SC006)

# Pseudo-rules emitted by the validator, not by a check function.
SCHEMA_ID = "SC000"
INPUT_ID = "SC900"


def run_rules(card: dict) -> list[Finding]:
    """Run every logic rule on a schema-valid, placeholder-free card."""
    findings: list[Finding] = []
    for rule in LOGIC_RULES:
        try:
            findings.extend(rule.check(card))
        except (ValueError, ArithmeticError, StatisticsError) as exc:
            # Extreme numbers (1e308, 10**310, n_trials 1e16) must give a finding, not a
            # traceback. The rule could not be evaluated, so the card cannot pass it.
            findings.append(
                Finding(
                    rule.id,
                    rule.name,
                    ERROR,
                    "(card)",
                    f"cannot evaluate: {type(exc).__name__}: {str(exc)[:200]}",
                )
            )
    return findings
