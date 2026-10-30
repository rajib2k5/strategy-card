from __future__ import annotations

import math

import numpy as np
import pytest
import yaml

from strategy_card.cli import main
from strategy_card.fill import build_card, read_returns, read_trials
from strategy_card.loader import InputError
from strategy_card.rules import PLACEHOLDER
from strategy_card.validate import validate_card, validate_file

from .conftest import ROOT

EX = ROOT / "examples"


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


@pytest.fixture
def returns_csv(tmp_path):
    rng = np.random.default_rng(3)
    lines = ["date,return"]
    # Weekday dates (equity-like), so that 252 periods per year is the right label.
    days = np.busday_offset("2019-01-01", np.arange(600), roll="forward")
    for day, r in zip(days, rng.normal(0.0005, 0.01, 600), strict=True):
        lines.append(f"{day},{r:.8f}")
    return write(tmp_path, "r.csv", "\n".join(lines) + "\n")


def test_fill_example_writes_a_card_that_fails_only_on_placeholders(tmp_path, capsys):
    out = tmp_path / "card.yaml"
    code = main(
        [
            "fill",
            "--periods-per-year",
            "252",
            "--returns",
            str(EX / "returns.csv"),
            "--trials",
            str(EX / "trials.csv"),
            "--split-date",
            "2022-01-03",
            "-o",
            str(out),
        ]
    )
    assert code == 0
    report = validate_file(out)
    assert report.exit_code() == 1
    assert {f.id for f in report.findings} == {"SC006"}
    paths = {f.path for f in report.findings}
    for human_field in (
        "costs.fees_bps",
        "costs.slippage_bps",
        "costs.model",
        "data.point_in_time",
        "data.survivorship.handling",
        "data.sources.0.name",
        "strategy.name",
    ):
        assert human_field in paths


def test_filled_card_never_defaults_costs_or_data_handling(returns_csv, tmp_path):
    trials = write(tmp_path, "t.csv", "sharpe_annualized\n0.5\n0.9\n")
    card = build_card(read_returns(returns_csv), *read_trials(trials), 252)
    assert card["costs"]["fees_bps"] == PLACEHOLDER
    assert card["costs"]["slippage_bps"] == PLACEHOLDER
    assert card["data"]["point_in_time"] == PLACEHOLDER
    assert card["data"]["survivorship"]["handling"] == PLACEHOLDER


def test_filled_card_validates_after_human_fills_fields(returns_csv, tmp_path):
    trials = write(tmp_path, "t.csv", "sharpe_annualized\n0.5\n0.9\n0.7\n")
    from datetime import date

    card = build_card(
        read_returns(returns_csv), *read_trials(trials), 252, split_date=date(2020, 6, 1)
    )
    card["strategy"] = {"name": "x", "description": "y"}
    card["costs"] = {"fees_bps": 1, "slippage_bps": 1, "model": "flat"}
    card["data"] = {
        "point_in_time": "as_reported",
        "survivorship": {"handling": "not_applicable", "notes": "single series"},
        "sources": [{"name": "synthetic"}],
    }
    report = validate_card(card)
    assert report.errors == []
    assert card["performance"]["deflated_sharpe"]["n_trials"] == 3 == card["trials"]["count"]


def test_fill_sharpe_and_dsr_are_internally_consistent(returns_csv, tmp_path):
    trials = write(tmp_path, "t.csv", "trial,sharpe_annualized\na,0.2\nb,0.8\nc,1.1\n")
    rows = read_returns(returns_csv)
    card = build_card(rows, *read_trials(trials), 252)
    x = np.array([r.value for r in rows])
    expected = x.mean() / x.std(ddof=1) * math.sqrt(252)
    perf = card["performance"]
    assert perf["sharpe"]["in_sample"] == pytest.approx(expected, abs=5e-5)  # 4-dp output (round 3)
    assert perf["deflated_sharpe"]["sharpe_annualized"] == pytest.approx(expected, abs=5e-5)
    assert perf["deflated_sharpe"]["n_observations"] == 600
    assert 0 <= perf["deflated_sharpe"]["value"] <= 1


def test_split_date_partitions_returns(returns_csv, tmp_path):
    from datetime import date

    trials = write(tmp_path, "t.csv", "sharpe_per_period\n0.01\n0.05\n")
    rows = read_returns(returns_csv)
    card = build_card(rows, *read_trials(trials), 252, split_date=date(2020, 1, 1))
    tw = card["test_window"]
    assert tw["in_sample"] == {"start": "2019-01-01", "end": "2019-12-31"}
    assert tw["out_of_sample"]["start"] == "2020-01-01"
    assert card["performance"]["deflated_sharpe"]["n_observations"] == 261  # weekdays of 2019


def test_without_split_oos_is_placeholder(returns_csv, tmp_path):
    trials = write(tmp_path, "t.csv", "sharpe_annualized\n1.0\n")
    card = build_card(read_returns(returns_csv), *read_trials(trials), 252)
    assert card["performance"]["sharpe"]["out_of_sample"] == PLACEHOLDER
    assert card["test_window"]["out_of_sample"] == {"start": PLACEHOLDER, "end": PLACEHOLDER}
    assert "deflated_sharpe" not in card["performance"]  # one trial: no DSR


def test_trial_unit_must_be_explicit(tmp_path):
    with pytest.raises(InputError, match="exactly one"):
        read_trials(write(tmp_path, "t.csv", "trial,sharpe\n1,0.5\n"))
    with pytest.raises(InputError, match="exactly one"):
        read_trials(write(tmp_path, "t.csv", "sharpe_annualized,sharpe_per_period\n1,0.1\n"))


def test_trial_unit_is_read_from_header(tmp_path):
    assert read_trials(write(tmp_path, "a.csv", "sharpe_annualized\n1\n2\n"))[1] == "annualized"
    assert read_trials(write(tmp_path, "p.csv", "sharpe_per_period\n1\n2\n"))[1] == "per_period"


def test_annualized_and_per_period_trial_logs_give_same_card(returns_csv, tmp_path):
    ann = [0.3, 0.9, 1.4, 0.1]
    a = write(tmp_path, "a.csv", "sharpe_annualized\n" + "\n".join(map(str, ann)) + "\n")
    p = write(
        tmp_path,
        "p.csv",
        "sharpe_per_period\n" + "\n".join(repr(s / math.sqrt(252)) for s in ann) + "\n",
    )
    rows = read_returns(returns_csv)
    ca = build_card(rows, *read_trials(a), 252)["performance"]["deflated_sharpe"]
    cp = build_card(rows, *read_trials(p), 252)["performance"]["deflated_sharpe"]
    assert ca["value"] == pytest.approx(cp["value"], rel=1e-12)


@pytest.mark.parametrize(
    "text,match",
    [
        ("day,ret\n2020-01-01,0.1\n", "missing column"),
        ("date,return\n2020-01-01,0.1\n", "at least two"),
        ("date,return\n2020-01-02,0.1\n2020-01-01,0.2\n", "increasing"),
        ("date,return\n2020-01-01,0.1\n2020-01-01,0.2\n", "increasing"),
        ("date,return\n2020-01-01,abc\n2020-01-02,0.2\n", "not a number"),
        ("date,return\n2020-01-01,nan\n2020-01-02,0.2\n", "not finite"),
        ("date,return\n01/02/2020,0.1\n2020-01-02,0.2\n", "YYYY-MM-DD"),
        ("", "no header"),
    ],
)
def test_bad_returns_csv(tmp_path, text, match):
    with pytest.raises(InputError, match=match):
        read_returns(write(tmp_path, "r.csv", text))


def test_constant_returns_are_refused(tmp_path):
    # calendar-daily dates with a weekend, so 365 passes the frequency check
    text = "date,return\n2020-01-04,0.1\n2020-01-05,0.1\n2020-01-06,0.1\n"
    rows = read_returns(write(tmp_path, "r.csv", text))
    with pytest.raises(InputError, match="dispersion"):
        build_card(rows, [0.1], "annualized", 365)


def test_comment_lines_are_skipped(tmp_path):
    rows = read_returns(
        write(tmp_path, "r.csv", "# synthetic\ndate,return\n2020-01-01,0.1\n2020-01-02,0.2\n")
    )
    assert len(rows) == 2


def test_fill_refuses_to_overwrite_without_force(returns_csv, tmp_path, capsys):
    trials = write(tmp_path, "t.csv", "sharpe_annualized\n1\n2\n")
    out = write(tmp_path, "card.yaml", "hand edited\n")
    args = ["fill", "--returns", str(returns_csv), "--trials", str(trials), "-o", str(out)]
    args += ["--periods-per-year", "252"]
    assert main(args) == 2
    assert out.read_text() == "hand edited\n"
    assert main([*args, "--force"]) == 0
    assert yaml.safe_load(out.read_text())["trials"]["count"] == 2


def test_fill_bad_input_exits_2_and_writes_nothing(tmp_path, capsys):
    out = tmp_path / "card.yaml"
    r = write(tmp_path, "r.csv", "date,return\n")
    t = write(tmp_path, "t.csv", "sharpe_annualized\n1\n")
    args = ["fill", "--returns", str(r), "--trials", str(t), "-o", str(out)]
    assert main([*args, "--periods-per-year", "252"]) == 2
    assert not out.exists()


def test_fill_rejects_bad_split_date(returns_csv, tmp_path, capsys):
    t = write(tmp_path, "t.csv", "sharpe_annualized\n1\n")
    out = tmp_path / "c.yaml"
    base = ["fill", "--returns", str(returns_csv), "--trials", str(t), "-o", str(out)]
    base += ["--periods-per-year", "252"]
    assert main([*base, "--split-date", "2020-13-01"]) == 2
    assert main([*base, "--split-date", "2030-01-01"]) == 2  # leaves no OOS returns
    assert not out.exists()


def test_example_inputs_are_labelled_synthetic():
    for name in ("returns.csv", "trials.csv"):
        assert "SYNTHETIC" in (EX / name).read_text().splitlines()[0]


def test_example_inputs_are_reproducible(tmp_path):
    import runpy
    import shutil

    before = {n: (EX / n).read_bytes() for n in ("returns.csv", "trials.csv")}
    script = ROOT / "scripts" / "make_synthetic.py"
    try:
        runpy.run_path(str(script), run_name="__main__")
        after = {n: (EX / n).read_bytes() for n in ("returns.csv", "trials.csv")}
    finally:
        for n, b in before.items():
            (EX / n).write_bytes(b)
    assert after == before
    del shutil


def test_oversized_csv_field_is_input_error_not_crash(tmp_path):
    p = write(tmp_path, "r.csv", "date,return\n2020-01-01," + "9" * 200_000 + "\n")
    with pytest.raises(InputError):
        read_returns(p)


def test_nul_byte_in_csv_is_input_error(tmp_path):
    p = write(tmp_path, "t.csv", "sharpe_annualized\n1\x00\n2\n")
    with pytest.raises(InputError):
        read_trials(p)


# ---- review item 1: periods per year is required and checked against the data
def dated_csv(tmp_path, days, name="r.csv"):
    """Returns CSV with one row per date in ``days`` (deterministic values)."""
    rng = np.random.default_rng(5)
    lines = ["date,return"] + [
        f"{d},{r:.8f}" for d, r in zip(days, rng.normal(0.01, 0.04, len(days)), strict=True)
    ]
    return write(tmp_path, name, "\n".join(lines) + "\n")


def month_ends(n):
    import datetime as dt

    out, y, m = [], 2010, 1
    for _ in range(n):
        nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
        out.append((nxt - dt.timedelta(days=1)).isoformat())
        y, m = nxt.year, nxt.month
    return out


def weekdays(n):
    import datetime as dt

    out, d = [], dt.date(2015, 1, 2)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def test_periods_per_year_flag_is_required(tmp_path, capsys):
    t = write(tmp_path, "t.csv", "sharpe_annualized\n1\n")
    r = dated_csv(tmp_path, weekdays(300))
    with pytest.raises(SystemExit) as exc:
        main(["fill", "--returns", str(r), "--trials", str(t), "-o", str(tmp_path / "c.yaml")])
    assert exc.value.code == 2
    assert "periods-per-year" in capsys.readouterr().err


def calendar_days(n, start="2016-01-01"):
    import datetime as dt

    d0 = dt.date.fromisoformat(start)
    return [(d0 + dt.timedelta(days=i)).isoformat() for i in range(n)]


def weekly(n):
    import datetime as dt

    d0 = dt.date(2010, 1, 4)
    return [(d0 + dt.timedelta(days=7 * i)).isoformat() for i in range(n)]


# Round 2: ppy is inferred as (rows - 1) / calendar years spanned.
@pytest.mark.parametrize(
    "days,expected,rel",
    [
        (weekdays(500), 260.9, 0.01),  # weekdays only: ~260.9 rows per year
        (month_ends(120), 12, 0.01),
        (weekly(520), 52.18, 0.001),
        (calendar_days(1462), 365.25, 1e-12),  # 2016-01-01..2020-01-01: exact
    ],
)
def test_infer_periods_per_year(days, expected, rel):
    from strategy_card.fill import infer_periods_per_year

    rows = read_returns_from_days(days)
    assert infer_periods_per_year(rows) == pytest.approx(expected, rel=rel)


def read_returns_from_days(days):
    from datetime import date

    from strategy_card.fill import ReturnRow

    return [ReturnRow(date.fromisoformat(d), 0.01 * (i % 3 - 1)) for i, d in enumerate(days)]


def test_monthly_data_with_252_is_refused(tmp_path, capsys):
    """The reported bug: monthly data, default 252 -> Sharpe 5.1, DSR 0.9998."""
    r = dated_csv(tmp_path, month_ends(120))
    t = write(tmp_path, "t.csv", "sharpe_annualized\n0.3\n0.9\n0.5\n")
    out = tmp_path / "c.yaml"
    args = ["fill", "--returns", str(r), "--trials", str(t), "-o", str(out)]
    assert main([*args, "--periods-per-year", "252"]) == 2
    err = capsys.readouterr().err
    assert "inferred" in err and "12" in err
    assert not out.exists()
    assert main([*args, "--periods-per-year", "12"]) == 0


@pytest.mark.parametrize(
    "days,given,ok",
    [
        (weekdays(400), 252, True),
        (weekdays(400), 230, True),  # 260.9 / 230 = 1.13
        (weekdays(400), 262, True),  # upper limit for data without weekends
        (weekdays(400), 215, False),  # 1.21
        (weekdays(400), 310, False),  # 1.19
        (weekdays(400), 365, False),  # equity daily labelled 365 (moved the sample D -> B)
        (weekdays(400), 52, False),
        (calendar_days(400), 365, True),  # crypto: weekends present
        (calendar_days(400), 252, False),
        (weekly(520), 52, True),
        (weekly(520), 46, True),  # 52.18 / 1.15 = 45.37
        (weekly(520), 45, False),
        (weekly(520), 59, True),  # 52.18 * 1.15 = 60.0
        (weekly(520), 61, False),
        (month_ends(60), 12, True),
        (month_ends(60), 52, False),
        (month_ends(60), 4, False),
    ],
)
def test_periods_per_year_mismatch_factor(tmp_path, days, given, ok):
    rows = read_returns(dated_csv(tmp_path, days))
    if ok:
        build_card(rows, [0.5, 1.0], "annualized", given)
    else:
        with pytest.raises(InputError, match="inferred|weekend"):
            build_card(rows, [0.5, 1.0], "annualized", given)


# ---- review item 5: annualized output fields are pinned to an oracle
def test_fill_annualized_dsr_fields_match_oracle(returns_csv, tmp_path):
    from scipy import stats as sps

    trials_ann = [0.2, 0.8, 1.1, 0.5]
    t = write(tmp_path, "t.csv", "sharpe_annualized\n" + "\n".join(map(str, trials_ann)) + "\n")
    d = build_card(read_returns(returns_csv), *read_trials(t), 252)["performance"][
        "deflated_sharpe"
    ]
    var_ann = float(np.var(trials_ann, ddof=1))
    g = 0.5772156649015329
    n = len(trials_ann)
    q = (1 - g) * sps.norm.ppf(1 - 1 / n) + g * sps.norm.ppf(1 - 1 / (n * math.e))
    sr0_ann = math.sqrt(var_ann / 252) * q * math.sqrt(252)
    assert d["trial_sharpe_variance_annualized"] == pytest.approx(
        var_ann, rel=1e-5
    )  # 6 significant digits
    assert d["sr_benchmark_annualized"] == pytest.approx(sr0_ann, abs=6e-5)  # 4 dp


# ---- review item 7: CSV inputs must be regular files
def test_fill_refuses_fifo_csv(tmp_path):
    import os
    import subprocess
    import sys

    fifo = tmp_path / "r.csv"
    os.mkfifo(fifo)
    t = write(tmp_path, "t.csv", "sharpe_annualized\n1\n")
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "strategy_card.cli",
            "fill",
            "--returns",
            str(fifo),
            "--trials",
            str(t),
            "--periods-per-year",
            "252",
            "-o",
            str(tmp_path / "c.yaml"),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert r.returncode == 2 and "regular file" in r.stderr


# ---- review item 8: comments only before the header; no ragged rows
def test_comment_after_header_is_refused(tmp_path):
    p = write(tmp_path, "t.csv", "# log\nsharpe_annualized\n1.2\n# -0.4 (dropped loser)\n0.3\n")
    with pytest.raises(InputError, match="allowed only before the header"):
        read_trials(p)


def test_comment_after_header_in_returns_is_refused(tmp_path):
    p = write(tmp_path, "r.csv", "date,return\n2020-01-01,0.1\n#2020-01-02,0.2\n2020-01-03,0.1\n")
    with pytest.raises(InputError, match="allowed only before the header"):
        read_returns(p)


def test_comments_and_blank_lines_before_header_are_allowed(tmp_path):
    p = write(tmp_path, "t.csv", "# synthetic\n\n# unit: annualized\nsharpe_annualized\n1\n2\n")
    assert read_trials(p) == ([1.0, 2.0], "annualized")


@pytest.mark.parametrize(
    "name,text",
    [
        ("t.csv", "trial,sharpe_annualized\n1,0.5\n2,0.7,extra\n"),
        ("r.csv", "date,return\n2020-01-01,0.1\n2020-01-02,0.2,0.3\n"),
    ],
)
def test_ragged_rows_are_refused(tmp_path, name, text):
    p = write(tmp_path, name, text)
    reader = read_trials if name == "t.csv" else read_returns
    with pytest.raises(InputError, match="field"):
        reader(p)


# ---- review item 12: no local paths in the card
def test_trials_log_stores_basename_only(returns_csv, tmp_path, capsys):
    sub = tmp_path / "home" / "someone" / "secret-project"
    sub.mkdir(parents=True)
    t = sub / "trials.csv"
    t.write_text("sharpe_annualized\n1\n2\n")
    out = tmp_path / "c.yaml"
    args = ["fill", "--returns", str(returns_csv), "--trials", str(t), "-o", str(out)]
    assert main([*args, "--periods-per-year", "252"]) == 0
    text = out.read_text()
    assert yaml.safe_load(text)["trials"]["log"] == "trials.csv"
    assert "someone" not in text


# ---- round 2: reviewer's sampling-frequency data
CSV = ROOT / "tests" / "fixtures" / "csv"


@pytest.mark.parametrize(
    "name,good,bad",
    [("twice-weekly.csv", 104, 252), ("every-other-day.csv", 126, 252)],
)
def test_reviewer_frequency_files(name, good, bad):
    rows = read_returns(CSV / name)
    build_card(rows, [0.5, 1.0], "annualized", good)
    with pytest.raises(InputError, match="inferred"):
        build_card(rows, [0.5, 1.0], "annualized", bad)


def test_365_needs_weekend_dates(tmp_path):
    rows = read_returns(dated_csv(tmp_path, weekdays(400)))
    with pytest.raises(InputError, match="weekend"):
        build_card(rows, [0.5, 1.0], "annualized", 365)


def test_sample_card_inputs_refuse_365():
    """The reviewer moved the sample card from D to B with --periods-per-year 365."""
    rows = read_returns(EX / "returns.csv")
    with pytest.raises(InputError):
        build_card(rows, [0.5, 1.0], "annualized", 365)


# ---- round 2: no traceback from fill arithmetic
def test_fill_huge_trial_sharpes_exit_2(returns_csv, tmp_path, capsys):
    t = write(tmp_path, "t.csv", "sharpe_annualized\n1e308\n-1e308\n")
    out = tmp_path / "c.yaml"
    args = ["fill", "--returns", str(returns_csv), "--trials", str(t), "-o", str(out)]
    assert main([*args, "--periods-per-year", "252"]) == 2
    assert "error" in capsys.readouterr().err
    assert not out.exists()


def test_build_card_wraps_arithmetic_errors(returns_csv, tmp_path):
    rows = read_returns(returns_csv)
    with pytest.raises(InputError):
        build_card(rows, [1e308, -1e308], "annualized", 252)


# ---- round 2: strict ISO dates and unique headers
@pytest.mark.parametrize(
    "bad", ["20200101", "2020-W01-1", "2020-1-1", "2020-01-01T00:00", " 2020-01-01x"]
)
def test_csv_dates_must_be_yyyy_mm_dd(tmp_path, bad):
    p = write(tmp_path, "r.csv", f"date,return\n{bad},0.1\n2021-01-02,0.2\n")
    with pytest.raises(InputError, match="YYYY-MM-DD"):
        read_returns(p)


@pytest.mark.parametrize("bad", ["20220103", "2022-W01-1"])
def test_split_date_must_be_yyyy_mm_dd(returns_csv, tmp_path, capsys, bad):
    t = write(tmp_path, "t.csv", "sharpe_annualized\n1\n")
    out = tmp_path / "c.yaml"
    args = ["fill", "--returns", str(returns_csv), "--trials", str(t), "-o", str(out)]
    assert main([*args, "--periods-per-year", "252", "--split-date", bad]) == 2
    assert "YYYY-MM-DD" in capsys.readouterr().err


@pytest.mark.parametrize(
    "name,text",
    [
        ("t.csv", "sharpe_annualized,sharpe_annualized\n1,2\n"),
        ("r.csv", "date,return,return\n2020-01-01,0.1,0.2\n2020-01-02,0.2,0.1\n"),
    ],
)
def test_duplicate_header_column_is_refused(tmp_path, name, text):
    reader = read_trials if name == "t.csv" else read_returns
    with pytest.raises(InputError, match="duplicate"):
        reader(write(tmp_path, name, text))


# ---- round 2: fill output is published exclusively (no check-then-write race)
def test_fill_refuses_dangling_symlink_without_force(returns_csv, tmp_path, capsys):
    import os

    link = tmp_path / "card.yaml"
    os.symlink(tmp_path / "missing-target.yaml", link)
    t = write(tmp_path, "t.csv", "sharpe_annualized\n1\n2\n")
    args = ["fill", "--returns", str(returns_csv), "--trials", str(t), "-o", str(link)]
    assert main([*args, "--periods-per-year", "252"]) == 2
    assert link.is_symlink() and not (tmp_path / "missing-target.yaml").exists()


def test_weekend_rule_applies_just_above_262(tmp_path):
    """280 is within factor 1.15 of 260.9, so only the weekend rule refuses it (kills M67)."""
    rows = read_returns(dated_csv(tmp_path, weekdays(400)))
    with pytest.raises(InputError, match="weekend"):
        build_card(rows, [0.5, 1.0], "annualized", 280)


# ---- round 3: fill output round-trips at 4 dp and never writes a card validate rejects
HUMAN = {
    "strategy": {"name": "x", "description": "y"},
    "costs": {"fees_bps": 1, "slippage_bps": 1, "model": "flat"},
    "data": {
        "point_in_time": "as_reported",
        "survivorship": {"handling": "not_applicable", "notes": "n"},
        "sources": [{"name": "s"}],
    },
}


def test_fill_numbers_are_rounded_to_4_dp_and_validate(returns_csv, tmp_path):
    from datetime import date

    t = write(tmp_path, "t.csv", "sharpe_annualized\n0.3\n0.9\n0.55\n1.2\n")
    card = build_card(read_returns(returns_csv), *read_trials(t), 252, split_date=date(2020, 6, 1))
    d = card["performance"]["deflated_sharpe"]
    for k in ("value", "sharpe_annualized", "sr_benchmark_annualized", "skew", "kurtosis_pearson"):
        assert d[k] == round(d[k], 4), k
    for k in ("in_sample", "out_of_sample"):
        v = card["performance"]["sharpe"][k]
        assert v == round(v, 4), k
    card.update(HUMAN)
    report = validate_card(yaml.safe_load(yaml.safe_dump(card)))
    assert report.errors == []


def test_fill_two_row_in_sample_is_accepted_and_kurtosis_is_clamped(tmp_path):
    from datetime import date

    days = weekdays(300)
    r = dated_csv(tmp_path, days)
    t = write(tmp_path, "t.csv", "sharpe_annualized\n0.3\n0.9\n")
    card = build_card(read_returns(r), *read_trials(t), 252, split_date=date.fromisoformat(days[2]))
    d = card["performance"]["deflated_sharpe"]
    assert d["n_observations"] == 2
    assert d["kurtosis_pearson"] >= d["skew"] ** 2 + 1  # 0.9999999999999998 clamped to 1.0


def test_fill_refuses_sharpe_above_schema_maximum(tmp_path, capsys):
    lines = ["date,return"] + [
        f"{d},{0.01 + (1e-9 if i % 2 else 0):.12f}" for i, d in enumerate(weekdays(300))
    ]
    r = write(tmp_path, "r.csv", "\n".join(lines) + "\n")
    t = write(tmp_path, "t.csv", "sharpe_annualized\n0.3\n0.9\n")
    out = tmp_path / "c.yaml"
    args = ["fill", "--returns", str(r), "--trials", str(t), "-o", str(out)]
    assert main([*args, "--periods-per-year", "252"]) == 2
    err = capsys.readouterr().err
    assert "SC000" in err and "1000" in err
    assert not out.exists()


def test_fill_refuses_in_sample_gap_that_trips_sc010(tmp_path, capsys):
    import datetime as dt

    is_days = weekdays(20)  # Jan 2015
    late_is = "2017-01-03"  # one more in-sample row two years later
    oos, d = [], dt.date(2017, 2, 1)
    while len(oos) < 4000:
        if d.weekday() < 5:
            oos.append(d.isoformat())
        d += dt.timedelta(days=1)
    r = dated_csv(tmp_path, [*is_days, late_is, *oos])
    t = write(tmp_path, "t.csv", "sharpe_annualized\n0.3\n0.9\n")
    out = tmp_path / "c.yaml"
    args = ["fill", "--returns", str(r), "--trials", str(t), "-o", str(out)]
    assert main([*args, "--periods-per-year", "252", "--split-date", "2017-02-01"]) == 2
    assert "SC010" in capsys.readouterr().err
    assert not out.exists()


def test_six_day_market_with_saturdays_accepts_313(tmp_path):
    """Mon-Sat data has Saturdays but no Sundays (kills X7: weekday >= 5 -> >= 6)."""
    import datetime as dt

    days, d = [], dt.date(2015, 1, 5)
    while len(days) < 600:
        if d.weekday() < 6:
            days.append(d.isoformat())
        d += dt.timedelta(days=1)
    rows = read_returns(dated_csv(tmp_path, days))
    build_card(rows, [0.5, 1.0], "annualized", 313)


def test_fill_two_valued_in_sample_sits_on_both_moment_bounds(tmp_path):
    """9 x +1% and 1 x -5%: skew -8/3, kurtosis 73/9 = skew^2 + 1 = the n = 10 maximum.

    Rounding the skew AWAY from zero (-2.6667) makes skew^2 + 1 exceed the kurtosis maximum,
    so no rounded kurtosis is valid. fill must round the skew toward zero and clamp the
    kurtosis into the bounds, and the draft must validate.
    """
    from datetime import date

    days = weekdays(300)
    rets = [0.01] * 9 + [-0.05] + [0.001 * ((i % 7) - 3) for i in range(290)]
    lines = ["date,return"] + [f"{d},{r:.8f}" for d, r in zip(days, rets, strict=True)]
    r = write(tmp_path, "r.csv", "\n".join(lines) + "\n")
    t = write(tmp_path, "t.csv", "sharpe_annualized\n0.3\n0.9\n")
    card = build_card(
        read_returns(r), *read_trials(t), 252, split_date=date.fromisoformat(days[10])
    )
    d = card["performance"]["deflated_sharpe"]
    assert d["n_observations"] == 10
    assert d["skew"] == -2.6666  # truncated toward zero, not -2.6667
    assert d["skew"] ** 2 + 1 <= d["kurtosis_pearson"] <= (100 - 30 + 3) / 9


def test_rounded_dsr_block_clamps_kurtosis_to_pearson_floor():
    """skew 0.1234 -> skew^2 + 1 = 1.01522756; kurtosis 1.01522757 rounds DOWN to 1.0152,
    below the bound. The block must clamp it up to 1.0153 (kills M90)."""
    from strategy_card.fill import _rounded_dsr_block
    from strategy_card.stats import DSRResult

    res = DSRResult(
        value=0.5,
        n_trials=5,
        n_observations=2000,
        sharpe_per_period=0.05,
        sharpe_annualized=0.05 * math.sqrt(252),
        sr_benchmark_per_period=0.0,
        sr_benchmark_annualized=0.0,
        trial_sharpe_variance_per_period=0.1 / 252,
        trial_sharpe_variance_annualized=0.1,
        skew=0.1234,
        kurtosis=1.01522757,
    )
    d = _rounded_dsr_block(res, 252)
    assert d["skew"] == 0.1234
    assert d["kurtosis_pearson"] == 1.0153
    assert d["kurtosis_pearson"] >= d["skew"] ** 2 + 1


@pytest.mark.parametrize("seed", range(12))
def test_fill_dsr_round_trips_exactly_at_4_dp(tmp_path, seed):
    """The written SR* and DSR equal the validator's recomputation from the WRITTEN inputs,
    rounded to 4 dp (kills M92: DSR from unrounded inputs)."""
    from strategy_card import rules as R

    rng = np.random.default_rng(100 + seed)
    days = weekdays(400)
    rets = rng.standard_t(4, 400) * 0.01 + 0.0008
    lines = ["date,return"] + [f"{d},{r:.8f}" for d, r in zip(days, rets, strict=True)]
    r = write(tmp_path, "r.csv", "\n".join(lines) + "\n")
    trials = rng.normal(0.8, 0.4, int(rng.integers(2, 60)))
    t = write(
        tmp_path, "t.csv", "sharpe_annualized\n" + "\n".join(f"{x:.6f}" for x in trials) + "\n"
    )
    card = yaml.safe_load(yaml.safe_dump(build_card(read_returns(r), *read_trials(t), 252)))
    d = card["performance"]["deflated_sharpe"]
    sr_star = R._recomputed_sr_star_pp(card, d) * math.sqrt(252)
    assert d["sr_benchmark_annualized"] == round(sr_star, 4)
    assert d["value"] == round(R.recomputed_dsr(card, d), 4)


# ---- fill clamps and rounds the DSR inputs so the written card validates
def test_single_spike_kurtosis_is_clamped_to_the_sc011_maximum(tmp_path, capsys):
    """n = 393 with one outlier: the kurtosis is 391.00255..., which rounds to 391.0026,
    above the SC011 maximum. fill must clamp it down so the draft still validates."""
    days = [str(d) for d in np.busday_offset("2019-01-01", np.arange(450), roll="forward")]
    split = days[393]
    lines = ["date,return"]
    for i, day in enumerate(days):
        if i < 393:
            value = 0.05 if i == 200 else 0.001
        else:
            value = 0.001 * (1 + (i % 3))
        lines.append(f"{day},{value}")
    returns = write(tmp_path, "r.csv", "\n".join(lines) + "\n")
    trials = write(tmp_path, "t.csv", "sharpe_annualized\n0.5\n1.0\n1.5\n")
    out = tmp_path / "c.yaml"
    args = ["fill", "--returns", str(returns), "--trials", str(trials), "-o", str(out)]
    assert main([*args, "--periods-per-year", "252", "--split-date", split]) == 0
    dsr = yaml.safe_load(out.read_text())["performance"]["deflated_sharpe"]
    assert dsr["n_observations"] == 393
    assert dsr["kurtosis_pearson"] == 391.0025
    assert {f.id for f in validate_file(out).findings} == {"SC006"}


def test_trial_variance_is_rounded_to_six_significant_figures(returns_csv):
    rows = read_returns(returns_csv)
    # sample variance is 0.000152415073923...; 4 dp would give 0.0002
    card = build_card(rows, [1.0, 1.0123456, 1.0246913], "annualized", 252)
    var = card["performance"]["deflated_sharpe"]["trial_sharpe_variance_annualized"]
    assert var == 0.000152415
