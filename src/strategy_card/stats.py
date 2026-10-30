"""Sharpe ratio, Probabilistic Sharpe Ratio and Deflated Sharpe Ratio — stdlib only.

Ported from sharpe-observatory ``scripts/metrics.py`` (same author, MIT), rewritten
without numpy/scipy.

References
----------
- Sharpe, W. F. (1994). "The Sharpe Ratio." Journal of Portfolio Management 21(1).
- Bailey, D. H. & Lopez de Prado, M. (2012). "The Sharpe Ratio Efficient Frontier."
  Journal of Risk 15(2). Probabilistic Sharpe Ratio (PSR).
- Bailey, D. H., Borwein, J., Lopez de Prado, M. & Zhu, Q. J. (2014). "Pseudo-Mathematics
  and Financial Charlatanism." Notices of the AMS 61(5). Expected maximum Sharpe ratio
  across N trials (False Strategy Theorem).
- Bailey, D. H. & Lopez de Prado, M. (2014). "The Deflated Sharpe Ratio." Journal of
  Portfolio Management 40(5). DSR = PSR evaluated at SR* = E[max SR_N].

Units
-----
Every Sharpe ratio that enters PSR, E[max SR] or DSR is PER-PERIOD (for daily data: per
day, not annualized). Annualized values exist only for display and for the card.
Converting a trial Sharpe: SR_pp = SR_ann / sqrt(ppy); its variance: V_pp = V_ann / ppy.
Kurtosis is PEARSON kurtosis (normal = 3), never excess kurtosis.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

EULER_MASCHERONI = 0.5772156649015329
_NORMAL = statistics.NormalDist()

ANNUALIZED = "annualized"
PER_PERIOD = "per_period"
TRIAL_UNITS = (ANNUALIZED, PER_PERIOD)


def _finite_list(values: Sequence[float]) -> list[float]:
    out = [float(v) for v in values]
    if not all(math.isfinite(v) for v in out):
        raise ValueError("values must be finite numbers")
    return out


def sharpe_per_period(returns: Sequence[float]) -> float:
    """Per-period Sharpe ratio: mean / sample stdev (ddof = 1) of per-period excess returns."""
    x = _finite_list(returns)
    if len(x) < 2:
        raise ValueError("need at least two returns for a Sharpe ratio")
    sd = statistics.stdev(x)
    if sd == 0.0:
        raise ValueError("returns have zero dispersion; Sharpe ratio is undefined")
    return statistics.fmean(x) / sd


def annualize(sr_per_period: float, periods_per_year: float) -> float:
    """SR_ann = SR_pp * sqrt(periods_per_year)."""
    return sr_per_period * math.sqrt(periods_per_year)


def deannualize(sr_annualized: float, periods_per_year: float) -> float:
    """SR_pp = SR_ann / sqrt(periods_per_year)."""
    return sr_annualized / math.sqrt(periods_per_year)


def moments(returns: Sequence[float]) -> tuple[float, float]:
    """Sample skewness and PEARSON kurtosis (normal = 3), moment estimators (bias = True).

    g3 = m3 / m2^1.5 and g4 = m4 / m2^2 with m_k = mean((x - mean(x))^k).
    """
    x = _finite_list(returns)
    if len(x) < 2:
        raise ValueError("need at least two returns for moments")
    mu = statistics.fmean(x)
    dev = [v - mu for v in x]
    n = len(x)
    m2 = math.fsum(d * d for d in dev) / n
    if m2 == 0.0:
        raise ValueError("returns have zero dispersion; moments are undefined")
    m3 = math.fsum(d * d * d for d in dev) / n
    m4 = math.fsum(d * d * d * d for d in dev) / n
    return m3 / m2**1.5, m4 / m2**2


def probabilistic_sharpe_ratio(
    sr: float, sr_star: float, n: int, skew: float, kurtosis: float
) -> float:
    """PSR(SR*) = Phi((SR - SR*) sqrt(n - 1) / sqrt(1 - g3 SR + (g4 - 1)/4 SR^2)).

    ``sr`` and ``sr_star`` are per-period; ``kurtosis`` is Pearson (normal = 3).
    """
    if n < 2:
        raise ValueError("PSR needs at least two observations")
    if not all(math.isfinite(v) for v in (sr, sr_star, skew, kurtosis)):
        raise ValueError("PSR inputs must be finite")
    variance_term = 1.0 - skew * sr + ((kurtosis - 1.0) / 4.0) * sr**2
    if variance_term <= 0.0:
        raise ValueError("PSR variance term is not positive; inputs are inconsistent")
    z = (sr - sr_star) * math.sqrt(n - 1) / math.sqrt(variance_term)
    return _NORMAL.cdf(z)


def expected_max_sharpe(n_trials: int, var_sr: float, mean_sr: float = 0.0) -> float:
    """Expected maximum Sharpe ratio across ``n_trials`` independent trials.

    E[max SR_N] = E[SR] + sqrt(V[SR]) ((1 - g) Z(1 - 1/N) + g Z(1 - 1/(N e)))

    g is the Euler-Mascheroni constant and Z the standard-normal quantile. Inputs are
    per-period. The DSR null uses ``mean_sr = 0``. N = 1 returns ``mean_sr`` (the maximum of
    one trial is that trial; the formula itself diverges at N = 1).
    """
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    if var_sr < 0:
        raise ValueError("var_sr must be >= 0")
    if n_trials == 1:
        return float(mean_sr)
    g = EULER_MASCHERONI
    q = (1.0 - g) * _NORMAL.inv_cdf(1.0 - 1.0 / n_trials) + g * _NORMAL.inv_cdf(
        1.0 - 1.0 / (n_trials * math.e)
    )
    return mean_sr + math.sqrt(var_sr) * q


@dataclass(frozen=True)
class DSRResult:
    value: float
    n_trials: int
    n_observations: int
    sharpe_per_period: float
    sharpe_annualized: float
    sr_benchmark_per_period: float
    sr_benchmark_annualized: float
    trial_sharpe_variance_per_period: float
    trial_sharpe_variance_annualized: float
    skew: float
    kurtosis: float


def deflated_sharpe_from_returns(
    returns: Sequence[float],
    trial_sharpes: Sequence[float],
    periods_per_year: float,
    trial_unit: str,
) -> DSRResult:
    """Deflated Sharpe Ratio of ``returns`` given the Sharpe ratios of every trial.

    ``returns`` are per-period simple excess returns of the chosen strategy.
    ``trial_sharpes`` holds one Sharpe ratio per variant tried, in ``trial_unit``
    (``"annualized"`` or ``"per_period"``). They are converted to per-period before use.
    The trial variance is the sample variance (ddof = 1). SR* uses a zero-mean null.
    """
    if trial_unit not in TRIAL_UNITS:
        raise ValueError(f"trial_unit must be one of {TRIAL_UNITS}, got {trial_unit!r}")
    if not (math.isfinite(periods_per_year) and periods_per_year > 0):
        raise ValueError("periods_per_year must be a positive finite number")
    trials = _finite_list(trial_sharpes)
    if len(trials) < 2:
        raise ValueError("DSR needs at least two trials (trial variance is undefined for one)")
    if trial_unit == ANNUALIZED:
        trials_pp = [deannualize(s, periods_per_year) for s in trials]
    else:
        trials_pp = trials
    var_pp = statistics.variance(trials_pp)

    x = _finite_list(returns)
    sr_pp = sharpe_per_period(x)
    skew, kurt = moments(x)
    sr_star = expected_max_sharpe(len(trials_pp), var_pp)
    value = probabilistic_sharpe_ratio(sr_pp, sr_star, len(x), skew, kurt)
    return DSRResult(
        value=value,
        n_trials=len(trials_pp),
        n_observations=len(x),
        sharpe_per_period=sr_pp,
        sharpe_annualized=annualize(sr_pp, periods_per_year),
        sr_benchmark_per_period=sr_star,
        sr_benchmark_annualized=annualize(sr_star, periods_per_year),
        trial_sharpe_variance_per_period=var_pp,
        trial_sharpe_variance_annualized=var_pp * periods_per_year,
        skew=skew,
        kurtosis=kurt,
    )
