"""Each logic rule: fires on the bad case, stays silent on the boundary-good case."""

from __future__ import annotations

import math

import pytest

from strategy_card import rules
from strategy_card.rules import ERROR, PLACEHOLDER, RULES, WARNING, find_placeholders, run_rules


def ids(card):
    return sorted(f.id for f in run_rules(card))


def test_base_card_is_clean(card):
    assert run_rules(card) == []


def test_rule_ids_are_unique_and_documented():
    from pathlib import Path

    rules_md = (Path(__file__).resolve().parent.parent / "docs" / "RULES.md").read_text()
    seen = [r.id for r in RULES]
    assert len(seen) == len(set(seen))
    for r in RULES:
        assert r.id in rules_md, f"{r.id} missing from docs/RULES.md"
        assert r.severity in (ERROR, WARNING)


# ---- SC001 window start < end
@pytest.mark.parametrize("window", ["in_sample", "out_of_sample"])
def test_sc001_window_start_equal_end_is_error(card, window):
    w = card["test_window"][window]
    w["end"] = w["start"]
    if window == "in_sample":
        card["test_window"]["out_of_sample"]["start"] = "2010-01-05"
    assert "SC001" in ids(card)


def test_sc001_window_reversed_is_error(card):
    card["test_window"]["out_of_sample"] = {"start": "2020-12-31", "end": "2018-01-02"}
    found = [f for f in run_rules(card) if f.id == "SC001"]
    assert len(found) == 1
    assert found[0].severity == ERROR
    assert found[0].path == "test_window.out_of_sample"


def test_sc001_one_day_window_is_fine(card):
    card["test_window"]["out_of_sample"] = {"start": "2018-01-02", "end": "2018-01-03"}
    assert "SC001" not in ids(card)


# ---- SC002 OOS start after IS end
def test_sc002_oos_starting_on_is_end_is_error(card):
    card["test_window"]["out_of_sample"]["start"] = card["test_window"]["in_sample"]["end"]
    assert "SC002" in ids(card)


def test_sc002_oos_overlapping_is_is_error(card):
    card["test_window"]["out_of_sample"]["start"] = "2015-01-01"
    assert "SC002" in ids(card)


def test_sc002_oos_starting_day_after_is_end_is_fine(card):
    card["test_window"]["out_of_sample"]["start"] = "2017-12-30"
    assert "SC002" not in ids(card)


# ---- SC003 DSR required when count > 1
def test_sc003_two_trials_without_dsr_is_error(card):
    card["trials"]["count"] = 2
    del card["performance"]["deflated_sharpe"]
    assert ids(card) == ["SC003"]


def test_sc003_one_trial_without_dsr_is_fine(card):
    card["trials"]["count"] = 1
    del card["performance"]["deflated_sharpe"]
    assert "SC003" not in ids(card)


# ---- SC004 n_trials must equal count
@pytest.mark.parametrize("n", [19, 21, 1])
def test_sc004_n_trials_mismatch_is_error(card, n):
    card["performance"]["deflated_sharpe"]["n_trials"] = n
    assert "SC004" in ids(card)


def test_sc004_single_trial_with_matching_dsr_is_fine(card):
    card["trials"]["count"] = 1
    card["performance"]["deflated_sharpe"]["n_trials"] = 1
    assert "SC004" not in ids(card)


# ---- SC005 non-finite numbers
@pytest.mark.parametrize(
    "path",
    [
        ("costs", "fees_bps"),
        ("performance", "sharpe", "in_sample"),
        ("performance", "deflated_sharpe", "value"),
        ("performance", "periods_per_year"),
    ],
)
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_sc005_non_finite_number_is_error(card, path, value):
    node = card
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    found = [f for f in run_rules(card) if f.id == "SC005"]
    assert found and found[0].path == ".".join(path)


# ---- SC006 placeholders
def test_sc006_placeholder_paths_are_reported(card):
    card["costs"]["fees_bps"] = PLACEHOLDER
    card["data"]["sources"][0]["name"] = PLACEHOLDER
    found = find_placeholders(card)
    assert sorted(f.path for f in found) == ["costs.fees_bps", "data.sources.0.name"]
    assert all(f.id == "SC006" and f.severity == ERROR for f in found)


def test_sc006_placeholder_embedded_in_text_is_reported(card):
    card["strategy"]["description"] = f"Momentum. {PLACEHOLDER}"
    assert [f.path for f in find_placeholders(card)] == ["strategy.description"]


def test_sc006_clean_card_has_no_placeholders(card):
    assert find_placeholders(card) == []


# ---- warnings
def test_sc101_zero_fees_and_zero_slippage_warns(card):
    card["costs"]["fees_bps"] = 0
    card["costs"]["slippage_bps"] = 0.0
    found = [f for f in run_rules(card) if f.id == "SC101"]
    assert found and found[0].severity == WARNING and found[0].flag == "zero-costs"


@pytest.mark.parametrize("fees,slip", [(0, 0.5), (0.5, 0), (0.25, 0.25)])
def test_sc101_total_cost_at_threshold_is_fine(card, fees, slip):
    card["costs"]["fees_bps"] = fees
    card["costs"]["slippage_bps"] = slip
    assert "SC101" not in ids(card)


@pytest.mark.parametrize("pit", ["restated", "unknown", "mixed"])
def test_sc102_non_point_in_time_warns(card, pit):
    card["data"]["point_in_time"] = pit
    assert ids(card) == ["SC102"]


def test_sc102_as_reported_is_fine(card):
    assert "SC102" not in ids(card)


@pytest.mark.parametrize("handling", ["survivor_only", "unknown"])
def test_sc103_survivorship_warns(card, handling):
    card["data"]["survivorship"]["handling"] = handling
    assert ids(card) == ["SC103"]


@pytest.mark.parametrize("handling", ["includes_delisted", "not_applicable"])
def test_sc103_handled_survivorship_is_fine(card, handling):
    card["data"]["survivorship"]["handling"] = handling
    assert "SC103" not in ids(card)


# SC104 boundary tests call the rule directly: an arbitrary DSR value no longer matches its
# own inputs, so through run_rules it would also (correctly) raise SC008.
def test_sc104_dsr_just_below_threshold_warns(card):
    card["performance"]["deflated_sharpe"]["value"] = 0.9499
    assert [f.id for f in rules.sc104_low_dsr(card)] == ["SC104"]


def test_sc104_dsr_at_threshold_is_fine(card):
    card["performance"]["deflated_sharpe"]["value"] = 0.95
    assert rules.sc104_low_dsr(card) == []


def test_sc104_no_dsr_with_one_trial_is_fine(card):
    card["trials"]["count"] = 1
    del card["performance"]["deflated_sharpe"]
    assert ids(card) == []


def test_sc105_oos_below_half_of_is_warns(card):
    card["performance"]["sharpe"] = {"in_sample": 2.0, "out_of_sample": 0.99}
    # Direct call: changing the in-sample Sharpe alone also (correctly) trips SC009.
    assert [f.id for f in rules.sc105_oos_decay(card)] == ["SC105"]


def test_sc105_oos_exactly_half_is_fine(card):
    card["performance"]["sharpe"] = {"in_sample": 2.0, "out_of_sample": 1.0}
    assert "SC105" not in ids(card)


def test_sc105_negative_is_sharpe_does_not_warn(card):
    card["performance"]["sharpe"] = {"in_sample": -0.5, "out_of_sample": -1.0}
    assert "SC105" not in ids(card)


def test_sc106_oos_shorter_than_a_year_warns(card):
    card["test_window"]["out_of_sample"] = {"start": "2018-01-02", "end": "2019-01-01"}  # 364 d
    assert ids(card) == ["SC106"]


def test_sc106_oos_of_365_days_is_fine(card):
    card["test_window"]["out_of_sample"] = {"start": "2018-01-02", "end": "2019-01-02"}
    assert "SC106" not in ids(card)


def test_multiple_rules_fire_together(card):
    card["costs"]["fees_bps"] = 0
    card["costs"]["slippage_bps"] = 0
    card["data"]["point_in_time"] = "unknown"
    card["trials"]["count"] = 3
    assert ids(card) == ["SC004", "SC101", "SC102"]


def test_sc105_zero_in_sample_sharpe_does_not_warn(card):
    """Decay from an in-sample Sharpe of 0 is meaningless (kills mutant M31)."""
    card["performance"]["sharpe"] = {"in_sample": 0.0, "out_of_sample": -0.5}
    assert "SC105" not in ids(card)


def test_zero_length_oos_window_is_only_sc001(card):
    """A zero-length window is SC001's job; SC106 must not double-report it (kills M32)."""
    card["test_window"]["out_of_sample"] = {"start": "2018-01-02", "end": "2018-01-02"}
    assert ids(card) == ["SC001"]


def test_sc104_applies_to_single_trial_dsr_too(card):
    """A reported DSR below 0.95 warns even when only one trial was run (kills M38)."""
    card["trials"]["count"] = 1
    card["performance"]["deflated_sharpe"].update(value=0.5, n_trials=1)
    assert [f.id for f in rules.sc104_low_dsr(card)] == ["SC104"]


# ---- SC101 negligible-cost threshold (review item 11)
@pytest.mark.parametrize("fees,slip", [(0, 0.0001), (0.2, 0.2999), (0.4999, 0)])
def test_sc101_negligible_total_cost_warns(card, fees, slip):
    card["costs"]["fees_bps"] = fees
    card["costs"]["slippage_bps"] = slip
    assert ids(card) == ["SC101"]


# ---- SC007-SC010: the DSR block is recomputed, not trusted (review item 2)
def dsr(card):
    return card["performance"]["deflated_sharpe"]


def test_sc007_overstated_benchmark_is_error(card):
    dsr(card)["sr_benchmark_annualized"] = 4.0
    assert "SC007" in ids(card)


def test_sc007_understated_benchmark_is_error(card):
    dsr(card)["sr_benchmark_annualized"] = 0.2
    assert ids(card) == ["SC007"]


def test_sc007_benchmark_within_tolerance_is_fine(card):
    dsr(card)["sr_benchmark_annualized"] += 0.00009
    assert "SC007" not in ids(card)


def test_sc007_benchmark_just_outside_tolerance_is_error(card):
    dsr(card)["sr_benchmark_annualized"] += 0.00011
    assert ids(card) == ["SC007"]


def test_sc008_inflated_value_is_error(card):
    dsr(card)["value"] = 0.999
    assert ids(card) == ["SC008"]


def test_sc008_value_within_tolerance_is_fine(card):
    dsr(card)["value"] -= 0.00009
    assert "SC008" not in ids(card)


def test_sc008_value_just_outside_tolerance_is_error(card):
    dsr(card)["value"] -= 0.00011
    assert "SC008" in ids(card)


def test_sc008_uses_recomputed_benchmark_not_stated_one(card):
    """Lying about SR* and recomputing the value from the lie must still fail."""
    dsr(card)["sr_benchmark_annualized"] = 0.0
    dsr(card)["value"] = 0.9999999
    assert ids(card) == ["SC007", "SC008"]


def test_sc008_impossible_moments_are_error(card):
    dsr(card)["skew"] = 50.0  # makes the PSR variance term negative
    found = [f for f in run_rules(card) if f.id == "SC008"]
    assert found and "variance" in found[0].message


def test_sc009_dsr_sharpe_must_match_in_sample_sharpe(card):
    card["performance"]["sharpe"]["in_sample"] = 1.3
    assert ids(card) == ["SC009"]


def test_sc009_within_tolerance_is_fine(card):
    card["performance"]["sharpe"]["in_sample"] = 1.20009
    assert "SC009" not in ids(card)


def test_sc010_n_observations_far_below_window_is_error(card):
    """The reported lie: 10 observations for an 8-year daily window."""
    dsr(card)["n_observations"] = 10
    assert "SC010" in ids(card)


def test_sc010_window_much_shorter_than_n_observations_is_error(card):
    card["test_window"]["in_sample"]["start"] = "2016-01-04"  # 2 years for 2016 obs
    assert ids(card) == ["SC010"]


def test_sc010_calendar_daily_data_with_252_is_now_an_error(card):
    """Spec change (round 2): factor 1.2, so 365-day data labelled 252 (ratio ~1.45) fails."""
    dsr(card)["n_observations"] = 2900
    assert "SC010" in [f.id for f in rules.sc010_dsr_observations(card)]


def test_dsr_rules_skip_non_finite_inputs_and_leave_them_to_sc005(card):
    dsr(card)["skew"] = math.nan
    assert ids(card) == ["SC005"]


def test_dsr_rules_silent_without_dsr(card):
    card["trials"]["count"] = 1
    del card["performance"]["deflated_sharpe"]
    for rid in ("SC007", "SC008", "SC009", "SC010"):
        assert rid not in ids(card)


@pytest.mark.parametrize(
    "field",
    [
        "sharpe_annualized",
        "sr_benchmark_annualized",
        "trial_sharpe_variance_annualized",
        "n_observations",
        "skew",
        "kurtosis_pearson",
    ],
)
def test_every_dsr_input_is_required_by_the_schema(card, field):
    """Each DSR input is needed to recompute it (kills mutant M60)."""
    from strategy_card.validate import validate_card

    del card["performance"]["deflated_sharpe"][field]
    found = validate_card(card).findings
    assert [(f.id, f.path) for f in found] == [("SC000", "performance.deflated_sharpe")]
    assert field in found[0].message


# ---- round 2: SC010 tightened to factor 1.2 with one period of slack
# Base IS window 2010-01-04..2017-12-29 = 2916 days; at 252/yr that is 2011.86 periods.
# Allowed intervals (n - 1): [2011.86 / 1.2 - 1, 2011.86 * 1.2 + 1] = [1675.55, 2415.23].
@pytest.mark.parametrize("n,fires", [(1676, True), (1677, False), (2416, False), (2417, True)])
def test_sc010_boundaries(card, n, fires):
    dsr(card)["n_observations"] = n
    assert bool(rules.sc010_dsr_observations(card)) is fires


@pytest.mark.parametrize(
    "start,end,ppy",
    [
        ("2020-01-31", "2020-02-29", 12),
        ("2020-03-31", "2020-06-30", 4),
        ("2020-01-03", "2020-01-10", 52),
    ],
)
def test_sc010_two_rows_of_coarse_data_is_not_an_error(card, start, end, ppy):
    """Fence-post: 2 rows span 1 period, not 2."""
    card["test_window"]["in_sample"] = {"start": start, "end": end}
    card["performance"]["periods_per_year"] = ppy
    dsr(card)["n_observations"] = 2
    assert rules.sc010_dsr_observations(card) == []


# ---- round 2: SC011 moments must be feasible
def test_sc011_pearson_bound_violated_is_error(card):
    dsr(card)["skew"] = 2.0
    dsr(card)["kurtosis_pearson"] = 4.99
    assert [f.id for f in rules.sc011_dsr_moments(card)] == ["SC011"]


def test_sc011_pearson_bound_met_exactly_is_fine(card):
    dsr(card)["skew"] = 2.0
    dsr(card)["kurtosis_pearson"] = 5.0
    assert rules.sc011_dsr_moments(card) == []


def test_sc011_skew_beyond_sample_bound_is_error(card):
    # max |skew| of n = 2016 points is (n - 2) / sqrt(n - 1) = 44.88
    dsr(card)["skew"] = 45.0
    dsr(card)["kurtosis_pearson"] = 45.0**2 + 1
    assert [f.id for f in rules.sc011_dsr_moments(card)] == ["SC011"]


def test_sc011_skew_inside_sample_bound_is_fine(card):
    dsr(card)["skew"] = -44.8
    dsr(card)["kurtosis_pearson"] = 44.8**2 + 1
    assert rules.sc011_dsr_moments(card) == []


def test_sc011_kurtosis_beyond_sample_bound_is_error(card):
    # max Pearson kurtosis of n points is (n^2 - 3n + 3) / (n - 1) = 2014.0005 for n = 2016
    dsr(card)["kurtosis_pearson"] = 2015.0
    assert [f.id for f in rules.sc011_dsr_moments(card)] == ["SC011"]


def test_sc011_reported_forgery_impossible_moments2():
    """atk/impossible_moments2.yaml: skew 51.86, kurtosis 1.0 for 1826 observations."""
    from .conftest import BASE_CARD

    c = __import__("copy").deepcopy(BASE_CARD)
    c["performance"]["deflated_sharpe"].update(
        skew=51.86, kurtosis_pearson=1.0, n_observations=1826
    )
    assert rules.sc011_dsr_moments(c)


# ---- round 2: SC012 trial variance must be > 0 when n_trials > 1
@pytest.mark.parametrize("var", [0.0, 1e-13])
def test_sc012_zero_trial_variance_with_many_trials_is_error(card, var):
    dsr(card)["trial_sharpe_variance_annualized"] = var
    assert [f.id for f in rules.sc012_trial_variance(card)] == ["SC012"]


def test_sc012_small_positive_variance_is_fine(card):
    dsr(card)["trial_sharpe_variance_annualized"] = 1e-9
    assert rules.sc012_trial_variance(card) == []


def test_sc012_single_trial_zero_variance_is_fine(card):
    card["trials"]["count"] = 1
    dsr(card).update(n_trials=1, trial_sharpe_variance_annualized=0.0)
    assert rules.sc012_trial_variance(card) == []


# ---- round 2: no tracebacks from rule arithmetic (cards that bypass the schema)
@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: (c["trials"].update(count=10**16), dsr(c).update(n_trials=10**16)),
        lambda c: dsr(c).update(n_observations=10**310),
        lambda c: (
            dsr(c).update(sharpe_annualized=1e308),
            c["performance"]["sharpe"].update(in_sample=1e308),
        ),
        lambda c: dsr(c).update(skew=1e308, kurtosis_pearson=1e308),
        lambda c: dsr(c).update(trial_sharpe_variance_annualized=1e308),
    ],
    ids=["n_trials_1e16", "n_obs_1e310", "sharpe_1e308", "moments_1e308", "variance_1e308"],
)
def test_rule_arithmetic_never_raises(card, mutate):
    mutate(card)
    found = run_rules(card)  # must not raise
    assert any(f.severity == ERROR for f in found)


def test_rule_that_raises_becomes_an_error_finding(card, monkeypatch):
    def boom(card):
        raise OverflowError("math range error")

    import dataclasses

    patched = tuple(
        dataclasses.replace(r, check=boom) if r.id == "SC009" else r for r in rules.LOGIC_RULES
    )
    monkeypatch.setattr(rules, "LOGIC_RULES", patched)
    found = [f for f in run_rules(card) if f.id == "SC009"]
    assert found and found[0].severity == ERROR and "OverflowError" in found[0].message


# ---- round 2: schema maxima
@pytest.mark.parametrize(
    "path,value",
    [
        (("trials", "count"), 10_000_001),
        (("performance", "deflated_sharpe", "n_trials"), 10_000_001),
        (("performance", "deflated_sharpe", "n_observations"), 100_000_001),
        (("performance", "periods_per_year"), 525_961),
        (("performance", "sharpe", "in_sample"), 1001.0),
        (("performance", "sharpe", "out_of_sample"), -1001.0),
        (("performance", "deflated_sharpe", "sharpe_annualized"), 1e308),
    ],
)
def test_schema_rejects_values_above_documented_maxima(card, path, value):
    from strategy_card.validate import validate_card

    node = card
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value
    found = validate_card(card).findings
    assert {f.id for f in found} == {"SC000"}
    assert ".".join(path) in {f.path for f in found}


@pytest.mark.parametrize(
    "path,value",
    [
        (("trials", "count"), 10_000_000),
        (("performance", "periods_per_year"), 525_960),
        (("performance", "sharpe", "out_of_sample"), -1000.0),
    ],
)
def test_schema_accepts_values_at_documented_maxima(card, path, value):
    from strategy_card.validate import schema_findings

    node = card
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value
    assert schema_findings(card) == []


# ---- round 3: tolerances are 4-dp rounding, and SC104 grades the recomputed DSR
def test_sc009_just_outside_tolerance_is_error(card):
    card["performance"]["sharpe"]["in_sample"] = 1.20011
    assert ids(card) == ["SC009"]


def test_sc104_uses_recomputed_dsr_when_stated_is_higher(card):
    """r3/tol_forged.yaml: stated 0.95, inputs give 0.9452 -> SC104 must still warn."""
    card["performance"]["sharpe"]["in_sample"] = 1.1676
    dsr(card).update(sharpe_annualized=1.1676, value=0.95)
    assert [f.id for f in rules.sc104_low_dsr(card)] == ["SC104"]
    assert "SC008" in ids(card)


def test_sc104_uses_stated_dsr_when_it_is_lower(card):
    dsr(card)["value"] = 0.9  # recomputed is 0.9546
    assert [f.id for f in rules.sc104_low_dsr(card)] == ["SC104"]


def test_sc104_recomputed_dsr_above_threshold_is_fine(card):
    assert rules.sc104_low_dsr(card) == []  # stated = recomputed = 0.9546


def test_sc011_pearson_tolerance_is_tight(card):
    """kurtosis 1e-4 below skew^2 + 1 must fire (kills X2: tolerance 1e-9 -> 1e-3)."""
    dsr(card)["skew"] = 2.0
    dsr(card)["kurtosis_pearson"] = 4.9999
    assert [f.id for f in rules.sc011_dsr_moments(card)] == ["SC011"]


def test_sc012_is_a_warning_not_an_error(card):
    """Round 3: identical honest trials give zero variance; SR* = 0 is then correct."""
    dsr(card)["trial_sharpe_variance_annualized"] = 0.0
    found = rules.sc012_trial_variance(card)
    assert [(f.id, f.severity, f.flag) for f in found] == [("SC012", WARNING, "same-trials")]
