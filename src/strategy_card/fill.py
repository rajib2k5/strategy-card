"""Build a draft card from a returns CSV and a trial-log CSV.

Fields the CSVs cannot tell us (costs, data handling, sources, names) are written as the
placeholder ``__FILL_ME__``. The draft therefore fails validation (SC006) until a human
fills them in. The filler never writes a favourable default.
"""

from __future__ import annotations

import csv
import io
import math
import os
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

import yaml

from . import SCHEMA_VERSION
from .loader import MAX_CSV_BYTES, InputError, read_capped
from .rules import PLACEHOLDER
from .stats import (
    ANNUALIZED,
    PER_PERIOD,
    annualize,
    deflated_sharpe_from_returns,
    sharpe_per_period,
)

TRIAL_COLUMNS = {"sharpe_annualized": ANNUALIZED, "sharpe_per_period": PER_PERIOD}

HEADER = (
    "# Draft strategy card written by `strategy-card fill`.\n"
    f"# Replace every {PLACEHOLDER} with a real value, then run `strategy-card validate`.\n"
    "# The card fails validation (SC006) while any placeholder is left.\n"
    "# Analysis only. Not investment advice.\n"
)


_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def parse_iso_date(raw: str) -> date:
    """Strict YYYY-MM-DD. ``date.fromisoformat`` alone accepts '20200101' and '2020-W01-1'
    on Python 3.11+ but not on 3.10, so the format is checked first."""
    if not _ISO_DATE.fullmatch(raw):
        raise ValueError(f"{raw[:40]!r} is not a YYYY-MM-DD date")
    return date.fromisoformat(raw)


@dataclass(frozen=True)
class ReturnRow:
    day: date
    value: float


def _float(raw: str, where: str) -> float:
    try:
        v = float(raw)
    except ValueError as exc:
        raise InputError(f"{where}: {raw[:40]!r} is not a number") from exc
    if not math.isfinite(v):
        raise InputError(f"{where}: value is not finite")
    return v


def _reader(path: str | os.PathLike) -> tuple[list[str], list[dict[str, str]]]:
    """Return (header, data rows).

    Lines that start with '#' (and blank lines) are allowed only BEFORE the header. A '#'
    line after the header is an error: commenting out a losing trial would lower N and
    raise the DSR. A row with more fields than the header is an error.
    """
    text = read_capped(path, MAX_CSV_BYTES)
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines) and (not lines[i].strip() or lines[i].lstrip().startswith("#")):
        i += 1
    body = lines[i:]
    for n, line in enumerate(body[1:], start=1):
        if line.lstrip().startswith("#"):
            raise InputError(
                f"{path}: data row {n} is a comment; comments are allowed only before the header"
            )
    reader = csv.DictReader(io.StringIO("".join(body), newline=""))
    try:
        if not reader.fieldnames:
            raise InputError(f"{path}: no header row")
        reader.fieldnames = [f.strip() for f in reader.fieldnames]
        dupes = sorted({f for f in reader.fieldnames if reader.fieldnames.count(f) > 1})
        if dupes:
            raise InputError(f"{path}: duplicate header column(s) {dupes}")
        rows = list(reader)
    except csv.Error as exc:  # e.g. a field larger than csv.field_size_limit()
        raise InputError(f"{path}: malformed CSV: {exc}") from exc
    for n, rec in enumerate(rows, start=1):
        if None in rec:
            raise InputError(f"{path}: data row {n} has more fields than the header")
    return list(reader.fieldnames), rows


def read_returns(path: str | os.PathLike) -> list[ReturnRow]:
    """Read ``date,return`` rows: ISO dates, strictly increasing, finite per-period returns."""
    header, records = _reader(path)
    missing = {"date", "return"} - set(header)
    if missing:
        raise InputError(f"{path}: missing column(s) {sorted(missing)}; need 'date,return'")
    rows: list[ReturnRow] = []
    for i, rec in enumerate(records, start=1):
        where = f"{path} data row {i}"
        raw_date = (rec.get("date") or "").strip()
        try:
            day = parse_iso_date(raw_date)
        except ValueError as exc:
            raise InputError(f"{where}: {exc}") from exc
        if rows and day <= rows[-1].day:
            raise InputError(f"{where}: dates must be strictly increasing")
        rows.append(ReturnRow(day, _float((rec.get("return") or "").strip(), where)))
    if len(rows) < 2:
        raise InputError(f"{path}: need at least two returns")
    return rows


def read_trials(path: str | os.PathLike) -> tuple[list[float], str]:
    """Read one Sharpe ratio per trial. The header names the unit; it is never guessed."""
    header, records = _reader(path)
    present = [c for c in TRIAL_COLUMNS if c in header]
    if len(present) != 1:
        raise InputError(
            f"{path}: need exactly one of the columns {sorted(TRIAL_COLUMNS)} "
            "so that the Sharpe unit is explicit"
        )
    column = present[0]
    values = [
        _float((rec.get(column) or "").strip(), f"{path} data row {i}")
        for i, rec in enumerate(records, start=1)
    ]
    if not values:
        raise InputError(f"{path}: the trial log has no rows")
    return values, TRIAL_COLUMNS[column]


PPY_MISMATCH_FACTOR = 1.15
DAYS_PER_YEAR = 365.25
WEEKDAYS_PER_YEAR = 262  # a value above this needs weekend dates in the data


def infer_periods_per_year(rows: list[ReturnRow]) -> float:
    """(rows - 1) / calendar years spanned: n rows span n - 1 periods."""
    years = (rows[-1].day - rows[0].day).days / DAYS_PER_YEAR
    return (len(rows) - 1) / years


def check_periods_per_year(rows: list[ReturnRow], given: float) -> None:
    if given > WEEKDAYS_PER_YEAR and not any(r.day.weekday() >= 5 for r in rows):
        raise InputError(
            f"--periods-per-year {given:g} needs weekend dates, but the returns have none. "
            "Daily equity data has about 252 periods per year."
        )
    inferred = infer_periods_per_year(rows)
    ratio = max(given / inferred, inferred / given)
    if ratio > PPY_MISMATCH_FACTOR:
        raise InputError(
            f"--periods-per-year {given:g} does not match the data: {len(rows)} rows over "
            f"{rows[0].day} to {rows[-1].day} imply about {inferred:.1f} periods per year "
            f"(inferred). The two differ by more than a factor of {PPY_MISMATCH_FACTOR}."
        )


def _window(rows: list[ReturnRow]) -> dict[str, str]:
    return {"start": rows[0].day.isoformat(), "end": rows[-1].day.isoformat()}


def _sharpe_ann(rows: list[ReturnRow], ppy: float, label: str) -> float:
    try:
        return annualize(sharpe_per_period([r.value for r in rows]), ppy)
    except ValueError as exc:
        raise InputError(f"{label}: {exc}") from exc


def build_card(
    returns: list[ReturnRow],
    trials: list[float],
    trial_unit: str,
    periods_per_year: float,
    split_date: date | None = None,
    trials_log: str | None = None,
) -> dict[str, Any]:
    """Draft card. Any arithmetic failure (overflow, domain error) is an InputError."""
    try:
        return _build_card(returns, trials, trial_unit, periods_per_year, split_date, trials_log)
    except (ValueError, ArithmeticError) as exc:  # StatisticsError is a ValueError
        raise InputError(f"cannot compute the card: {type(exc).__name__}: {exc}") from exc


def _build_card(
    returns: list[ReturnRow],
    trials: list[float],
    trial_unit: str,
    periods_per_year: float,
    split_date: date | None = None,
    trials_log: str | None = None,
) -> dict[str, Any]:
    if not (math.isfinite(periods_per_year) and periods_per_year > 0):
        raise InputError("--periods-per-year must be a positive number")
    check_periods_per_year(returns, periods_per_year)
    if split_date is None:
        in_rows, oos_rows = returns, []
    else:
        in_rows = [r for r in returns if r.day < split_date]
        oos_rows = [r for r in returns if r.day >= split_date]
        if len(in_rows) < 2 or len(oos_rows) < 2:
            raise InputError("--split-date must leave at least two returns on each side")

    sharpe: dict[str, Any] = {
        "in_sample": _sharpe_ann(in_rows, periods_per_year, "in-sample returns"),
        "out_of_sample": (
            _sharpe_ann(oos_rows, periods_per_year, "out-of-sample returns")
            if oos_rows
            else PLACEHOLDER
        ),
    }
    ppy_out = int(periods_per_year) if float(periods_per_year).is_integer() else periods_per_year
    performance: dict[str, Any] = {"periods_per_year": ppy_out, "sharpe": sharpe}
    if len(trials) >= 2:
        try:
            res = deflated_sharpe_from_returns(
                [r.value for r in in_rows], trials, periods_per_year, trial_unit
            )
        except ValueError as exc:
            raise InputError(f"cannot compute the deflated Sharpe ratio: {exc}") from exc
        performance["deflated_sharpe"] = {
            "value": res.value,
            "n_trials": res.n_trials,
            "sharpe_annualized": res.sharpe_annualized,
            "sr_benchmark_annualized": res.sr_benchmark_annualized,
            "trial_sharpe_variance_annualized": res.trial_sharpe_variance_annualized,
            "n_observations": res.n_observations,
            "skew": res.skew,
            "kurtosis_pearson": res.kurtosis,
            "method": (
                "Bailey & Lopez de Prado (2014): PSR at SR* = E[max SR] over n_trials, "
                "zero-mean null, sample variance of trial Sharpes, in-sample returns"
            ),
        }

    trials_block: dict[str, Any] = {"count": len(trials)}
    if trials_log:
        trials_block["log"] = trials_log

    return {
        "schema_version": SCHEMA_VERSION,
        "strategy": {"name": PLACEHOLDER, "description": PLACEHOLDER},
        "test_window": {
            "in_sample": _window(in_rows),
            "out_of_sample": (
                _window(oos_rows) if oos_rows else {"start": PLACEHOLDER, "end": PLACEHOLDER}
            ),
        },
        "costs": {"fees_bps": PLACEHOLDER, "slippage_bps": PLACEHOLDER, "model": PLACEHOLDER},
        "trials": trials_block,
        "performance": performance,
        "data": {
            "point_in_time": PLACEHOLDER,
            "survivorship": {"handling": PLACEHOLDER, "notes": PLACEHOLDER},
            "sources": [{"name": PLACEHOLDER}],
        },
    }


def dump_card(card: dict[str, Any]) -> str:
    return HEADER + yaml.safe_dump(card, sort_keys=False, allow_unicode=False, width=100)
