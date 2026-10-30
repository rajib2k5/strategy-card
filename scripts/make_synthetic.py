"""Write the SYNTHETIC example inputs: examples/returns.csv and examples/trials.csv.

The data is made up with a seeded random generator. It is not market data and comes from
no vendor. Re-running this script gives byte-identical files.

    python scripts/make_synthetic.py
"""

from __future__ import annotations

import math
import random
import statistics
from datetime import date, timedelta
from pathlib import Path

SEED = 20260101
START = date(2015, 1, 2)
N_DAYS = 2520  # ten years of weekdays
N_TRIALS = 25
OUT = Path(__file__).resolve().parent.parent / "examples"


def weekdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def main() -> None:
    rng = random.Random(SEED)
    days = weekdays(START, N_DAYS)
    returns = []
    for _ in days:
        r = rng.gauss(0.0009, 0.0095)
        if rng.random() < 0.01:  # rare down-jumps give negative skew and fat tails
            r -= abs(rng.gauss(0.03, 0.01))
        returns.append(r)

    OUT.mkdir(exist_ok=True)
    with open(OUT / "returns.csv", "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# SYNTHETIC DATA: seeded random numbers from scripts/make_synthetic.py.\n")
        fh.write("# Not market data. No vendor data. Per-period simple excess returns.\n")
        fh.write("date,return\n")
        for d, r in zip(days, returns, strict=True):
            fh.write(f"{d.isoformat()},{r:.8f}\n")

    split = days.index(date(2022, 1, 3))
    is_rets = returns[:split]
    chosen = statistics.fmean(is_rets) / statistics.stdev(is_rets) * math.sqrt(252)
    with open(OUT / "trials.csv", "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# SYNTHETIC DATA: seeded random numbers from scripts/make_synthetic.py.\n")
        fh.write("# One row per variant tried. Sharpe ratios are annualized.\n")
        fh.write("trial_id,lookback_days,sharpe_annualized\n")
        fh.write(f"1,60,{chosen:.6f}\n")  # the variant that was picked
        for i in range(2, N_TRIALS + 1):
            fh.write(f"{i},{20 + 10 * i},{rng.gauss(0.5, 0.25):.6f}\n")


if __name__ == "__main__":
    main()
