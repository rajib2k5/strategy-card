"""Statistics tests, pinned against independent oracles.

Oracle 1 (published worked example): Bailey, D. H. & Lopez de Prado, M. (2014), "The Deflated
Sharpe Ratio", Journal of Portfolio Management 40(5). The paper's numerical example uses
annualized SR = 2.5, T = 1250 daily observations (250 per year), skew = -3, Pearson
kurtosis = 10, N = 100 trials, and variance of the annualized trial Sharpes = 0.5. It
reports SR0 ~= 0.1132 (per period) and DSR ~= 0.9004.

Oracle 2 (hand computation): the four-point series in ``HAND`` below, worked on paper.

Oracle 3 (cross-check): numpy/scipy implementations written in this file. The shipped code
uses only the stdlib, so the two implementations share no code.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import stats as sps

from strategy_card import stats

EULER = 0.5772156649015329

# ---------------------------------------------------------------- hand-computed oracle
HAND = [0.01, -0.02, 0.03, 0.00]
# mean = 0.005; sum of squared deviations = 1.3e-3
# sample sd = sqrt(1.3e-3 / 3) = 0.0208166599...
# SR = 0.005 / 0.0208166599 = 0.2401922307...
# m2 = 3.25e-4, m3 = 0, m4 = 1.95625e-7 -> skew 0, Pearson kurtosis = 1.95625e-7 / 1.05625e-7
HAND_SR = 0.005 / math.sqrt(1.3e-3 / 3)
HAND_KURT = 1.95625e-7 / (3.25e-4**2)


def test_sharpe_per_period_matches_hand_computation():
    assert stats.sharpe_per_period(HAND) == pytest.approx(0.2401922307, rel=1e-9)
    assert stats.sharpe_per_period(HAND) == pytest.approx(HAND_SR, rel=1e-12)


def test_moments_match_hand_computation_and_kurtosis_is_pearson():
    skew, kurt = stats.moments(HAND)
    assert skew == pytest.approx(0.0, abs=1e-12)
    assert kurt == pytest.approx(1.8520710059, rel=1e-9)
    assert kurt == pytest.approx(HAND_KURT, rel=1e-12)


def test_normal_sample_has_pearson_kurtosis_near_three_not_zero():
    rng = np.random.default_rng(7)
    x = rng.normal(0.0, 0.01, 200_000).tolist()
    _, kurt = stats.moments(x)
    assert kurt == pytest.approx(3.0, abs=0.05)


def test_annualize_and_deannualize_round_trip():
    assert stats.annualize(0.1, 252) == pytest.approx(0.1 * math.sqrt(252))
    assert stats.deannualize(stats.annualize(0.1, 252), 252) == pytest.approx(0.1)
    assert stats.annualize(0.1, 12) == pytest.approx(0.1 * math.sqrt(12))


@pytest.mark.parametrize("bad", [[], [0.01], [0.01, 0.01, 0.01]])
def test_sharpe_undefined_raises(bad):
    with pytest.raises(ValueError):
        stats.sharpe_per_period(bad)


# ---------------------------------------------------------------- published worked example
PAPER = dict(sr_ann=2.5, ppy=250, n_obs=1250, skew=-3.0, kurt=10.0, n_trials=100, var_ann=0.5)


def test_expected_max_sharpe_matches_paper_sr0():
    var_pp = PAPER["var_ann"] / PAPER["ppy"]
    sr0 = stats.expected_max_sharpe(PAPER["n_trials"], var_pp)
    assert sr0 == pytest.approx(0.1132, abs=5e-5)


def test_deflated_sharpe_matches_paper_value():
    sr_pp = PAPER["sr_ann"] / math.sqrt(PAPER["ppy"])
    sr0 = stats.expected_max_sharpe(PAPER["n_trials"], PAPER["var_ann"] / PAPER["ppy"])
    dsr = stats.probabilistic_sharpe_ratio(sr_pp, sr0, PAPER["n_obs"], PAPER["skew"], PAPER["kurt"])
    assert dsr == pytest.approx(0.9004, abs=5e-4)


def test_paper_example_with_annualized_sr_plugged_in_raw_gives_wrong_answer():
    """Classic bug: annualized SR in a per-period formula. The right path must differ."""
    sr0 = stats.expected_max_sharpe(PAPER["n_trials"], PAPER["var_ann"])  # annualized var: wrong
    wrong = stats.probabilistic_sharpe_ratio(
        PAPER["sr_ann"], sr0, PAPER["n_obs"], PAPER["skew"], PAPER["kurt"]
    )
    assert abs(wrong - 0.9004) > 0.05


# ---------------------------------------------------------------- scipy/numpy cross-check
def oracle_sharpe(x):
    x = np.asarray(x, float)
    return x.mean() / x.std(ddof=1)


def oracle_moments(x):
    return float(sps.skew(x, bias=True)), float(sps.kurtosis(x, fisher=False, bias=True))


def oracle_psr(sr, sr_star, n, skew, kurt):
    z = (sr - sr_star) * math.sqrt(n - 1) / math.sqrt(1 - skew * sr + (kurt - 1) / 4 * sr**2)
    return float(sps.norm.cdf(z))


def oracle_emax(n, var, mean=0.0):
    q = (1 - EULER) * sps.norm.ppf(1 - 1 / n) + EULER * sps.norm.ppf(1 - 1 / (n * math.e))
    return mean + math.sqrt(var) * q


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_sharpe_and_moments_match_scipy(seed):
    rng = np.random.default_rng(seed)
    x = (rng.standard_t(4, 1500) * 0.01 + 0.0004).tolist()
    assert stats.sharpe_per_period(x) == pytest.approx(oracle_sharpe(x), rel=1e-10)
    s, k = stats.moments(x)
    os_, ok = oracle_moments(x)
    assert s == pytest.approx(os_, rel=1e-9, abs=1e-12)
    assert k == pytest.approx(ok, rel=1e-9)


@pytest.mark.parametrize("n", [2, 3, 10, 100, 1000, 10_000])
def test_expected_max_sharpe_matches_scipy(n):
    assert stats.expected_max_sharpe(n, 0.0004) == pytest.approx(oracle_emax(n, 0.0004), rel=1e-9)
    assert stats.expected_max_sharpe(n, 0.0004, mean_sr=0.01) == pytest.approx(
        oracle_emax(n, 0.0004, 0.01), rel=1e-9
    )


def test_expected_max_sharpe_single_trial_is_the_mean():
    assert stats.expected_max_sharpe(1, 0.5, mean_sr=0.02) == 0.02


def test_expected_max_sharpe_rejects_zero_trials():
    with pytest.raises(ValueError):
        stats.expected_max_sharpe(0, 0.1)


@pytest.mark.parametrize(
    "sr,sr_star,n,skew,kurt",
    [(0.1, 0.0, 500, -0.5, 5.0), (0.05, 0.06, 1000, 0.3, 3.2), (0.2, 0.1, 60, -1.0, 8.0)],
)
def test_psr_matches_scipy(sr, sr_star, n, skew, kurt):
    got = stats.probabilistic_sharpe_ratio(sr, sr_star, n, skew, kurt)
    assert got == pytest.approx(oracle_psr(sr, sr_star, n, skew, kurt), rel=1e-9, abs=1e-12)


def test_psr_rejects_non_positive_variance_term():
    with pytest.raises(ValueError):
        stats.probabilistic_sharpe_ratio(1.0, 0.0, 100, 5.0, 3.0)  # 1 - 5 + 0.5 < 0


def test_psr_rejects_too_few_observations():
    with pytest.raises(ValueError):
        stats.probabilistic_sharpe_ratio(0.1, 0.0, 1, 0.0, 3.0)


# ---------------------------------------------------------------- end-to-end DSR and units
def _sample(seed=11, n=1260, ppy=252):
    rng = np.random.default_rng(seed)
    returns = (rng.standard_t(5, n) * 0.008 + 0.0006).tolist()
    trial_ann = (rng.normal(0.4, 0.5, 40)).tolist()
    return returns, trial_ann, ppy


def test_dsr_from_returns_matches_scipy_assembly():
    returns, trial_ann, ppy = _sample()
    res = stats.deflated_sharpe_from_returns(returns, trial_ann, ppy, "annualized")

    sr = oracle_sharpe(returns)
    skew, kurt = oracle_moments(returns)
    var_pp = float(np.var(np.asarray(trial_ann) / math.sqrt(ppy), ddof=1))
    sr0 = oracle_emax(len(trial_ann), var_pp)
    expected = oracle_psr(sr, sr0, len(returns), skew, kurt)

    assert res.value == pytest.approx(expected, rel=1e-9, abs=1e-12)
    assert res.n_trials == 40
    assert res.n_observations == 1260
    assert res.sharpe_per_period == pytest.approx(sr, rel=1e-10)
    assert res.sharpe_annualized == pytest.approx(sr * math.sqrt(ppy), rel=1e-10)
    assert res.sr_benchmark_per_period == pytest.approx(sr0, rel=1e-9)
    assert res.sr_benchmark_annualized == pytest.approx(sr0 * math.sqrt(ppy), rel=1e-9)
    assert res.trial_sharpe_variance_per_period == pytest.approx(var_pp, rel=1e-10)
    assert res.trial_sharpe_variance_annualized == pytest.approx(var_pp * ppy, rel=1e-10)
    assert res.skew == pytest.approx(skew, rel=1e-9)
    assert res.kurtosis == pytest.approx(kurt, rel=1e-9)


def test_dsr_same_answer_whether_trials_given_annualized_or_per_period():
    returns, trial_ann, ppy = _sample()
    trial_pp = [s / math.sqrt(ppy) for s in trial_ann]
    a = stats.deflated_sharpe_from_returns(returns, trial_ann, ppy, "annualized")
    p = stats.deflated_sharpe_from_returns(returns, trial_pp, ppy, "per_period")
    assert a.value == pytest.approx(p.value, rel=1e-12)
    assert a.sr_benchmark_per_period == pytest.approx(p.sr_benchmark_per_period, rel=1e-12)


def test_dsr_annualized_trials_mislabelled_as_per_period_changes_the_answer():
    returns, trial_ann, ppy = _sample()
    right = stats.deflated_sharpe_from_returns(returns, trial_ann, ppy, "annualized")
    wrong = stats.deflated_sharpe_from_returns(returns, trial_ann, ppy, "per_period")
    # Mislabelling inflates the trial variance by ppy, so SR* jumps and DSR collapses.
    assert wrong.sr_benchmark_per_period == pytest.approx(
        right.sr_benchmark_per_period * math.sqrt(ppy), rel=1e-9
    )
    assert wrong.value < right.value


def test_dsr_depends_on_periods_per_year_through_trial_conversion():
    returns, trial_ann, _ = _sample()
    d252 = stats.deflated_sharpe_from_returns(returns, trial_ann, 252, "annualized")
    d52 = stats.deflated_sharpe_from_returns(returns, trial_ann, 52, "annualized")
    assert d252.sr_benchmark_per_period < d52.sr_benchmark_per_period


def test_dsr_rejects_unknown_unit():
    returns, trial_ann, ppy = _sample()
    with pytest.raises(ValueError):
        stats.deflated_sharpe_from_returns(returns, trial_ann, ppy, "yearly")


def test_dsr_needs_at_least_two_trials():
    returns, _, ppy = _sample()
    with pytest.raises(ValueError):
        stats.deflated_sharpe_from_returns(returns, [1.0], ppy, "annualized")


def test_dsr_rises_with_fewer_trials_all_else_equal():
    """More trials -> higher SR* -> lower DSR, even though the 100-trial variance is smaller."""
    returns, _, ppy = _sample()
    few = stats.deflated_sharpe_from_returns(returns, [0.0, 1.0], ppy, "annualized")
    many = stats.deflated_sharpe_from_returns(returns, [0.0, 1.0] * 50, ppy, "annualized")
    assert many.sr_benchmark_per_period > few.sr_benchmark_per_period
    assert many.value < few.value


def test_psr_rejects_exactly_zero_variance_term():
    """1 - skew*SR + (kurt-1)/4*SR^2 == 0 at SR=1, skew=1, kurt=1 (kills mutant M33)."""
    with pytest.raises(ValueError):
        stats.probabilistic_sharpe_ratio(1.0, 0.0, 100, 1.0, 1.0)
