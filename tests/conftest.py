from __future__ import annotations

import copy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"

BASE_CARD = {
    "schema_version": "0.1",
    "strategy": {"name": "Test strategy", "description": "A clean card used as a test baseline."},
    "test_window": {
        "in_sample": {"start": "2010-01-04", "end": "2017-12-29"},
        "out_of_sample": {"start": "2018-01-02", "end": "2020-12-31"},
    },
    "costs": {"fees_bps": 1.0, "slippage_bps": 2.0, "model": "per side, on traded notional"},
    "trials": {"count": 20, "log": "trials.csv"},
    "performance": {
        "periods_per_year": 252,
        "sharpe": {"in_sample": 1.2, "out_of_sample": 0.9},
        # Numbers from the scipy oracle (see tests/test_stats.py), not from strategy_card:
        # SR 1.2 ann, var 0.1 ann, N 20, n 2016, skew 0, kurt 3 -> SR* 0.601057, DSR 0.954604.
        "deflated_sharpe": {
            "value": 0.954603858439656,
            "n_trials": 20,
            "sharpe_annualized": 1.2,
            "sr_benchmark_annualized": 0.6010566292524715,
            "trial_sharpe_variance_annualized": 0.1,
            "n_observations": 2016,
            "skew": 0.0,
            "kurtosis_pearson": 3.0,
        },
    },
    "data": {
        "point_in_time": "as_reported",
        "survivorship": {"handling": "includes_delisted", "notes": "delisted names kept"},
        "sources": [{"name": "Synthetic", "license": "CC0"}],
    },
}


@pytest.fixture
def card():
    """A fresh deep copy of a card that passes every rule with no warnings."""
    return copy.deepcopy(BASE_CARD)
