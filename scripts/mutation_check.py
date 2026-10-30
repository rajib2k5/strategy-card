"""Hand-rolled mutation check for the rules, grading, DSR and input-safety code.

Each mutation replaces one exact snippet (it must occur exactly once), runs the test suite,
and restores the file. A mutation is KILLED only when pytest exits with code 1 (tests ran
and failed). Exit codes 2+ (collection or import error, usage error) are reported as ERROR
and a hang as TIMEOUT; neither counts as killed. Source
files are hashed before and after; the script exits non-zero if any file is not restored
byte for byte, or if any mutation survives.

Safety: before a file is mutated, its original is copied to ``<file>.mutbak``. The backup
is restored in ``finally`` and on SIGINT/SIGTERM, then deleted. If a stale ``.mutbak``
exists at startup (an earlier run was killed with SIGKILL), the script refuses to run.

    python scripts/mutation_check.py
"""

from __future__ import annotations

import hashlib
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "strategy_card"

R, G, S, V, L, B, F = (
    "rules.py",
    "grading.py",
    "stats.py",
    "validate.py",
    "loader.py",
    "badge.py",
    "fill.py",
)

# (id, file, old, new, description)
# fmt: off
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    ("M01", R, "if _d(oos_start) > _d(is_end):", "if _d(oos_start) >= _d(is_end):",
     "SC002: flip > to >= (OOS may start on IS end)"),
    ("M02", R, 'if not _d(w["start"]) < _d(w["end"]):', 'if not _d(w["start"]) <= _d(w["end"]):',
     "SC001: allow start == end"),
    ("M03", R, "if count > 1 and", "if count > 2 and", "SC003: off-by-one on trial count"),
    ("M04", R, 'if dsr["n_trials"] != count:', 'if dsr["n_trials"] > count:',
     "SC004: only flag n_trials > count"),
    ("M05", R, "DSR_THRESHOLD = 0.95", "DSR_THRESHOLD = 0.90", "SC104: change 0.95 to 0.90"),
    ("M06", R, 'dsr["value"] < DSR_THRESHOLD', 'dsr["value"] <= DSR_THRESHOLD',
     "SC104: flip < to <="),
    ("M07", R, "    SC101,\n", "", "drop rule SC101 from RULES"),
    ("M08", R, "    SC002,\n", "", "drop rule SC002 from RULES"),
    ("M09", R, "if total < NEGLIGIBLE_COST_BPS:", "if total <= NEGLIGIBLE_COST_BPS:",
     "SC101: < -> <= at the 0.5 bps threshold"),
    ("M10", R, 'if pit in ("restated", "unknown", "mixed"):', 'if pit in ("restated", "unknown"):',
     "SC102: forget 'mixed'"),
    ("M11", R, "if 0 < days < MIN_OOS_DAYS:", "if 0 < days <= MIN_OOS_DAYS:",
     "SC106: off-by-one on 365 days"),
    ("M12", R, "not math.isfinite(value)", "math.isnan(value)", "SC005: miss infinities"),
    ("M13", R, "OOS_DECAY_RATIO = 0.5", "OOS_DECAY_RATIO = 0.4", "SC105: change ratio"),
    ("M14", G, "    if errors:\n", "    if errors > 1:\n", "grade: one error is not F"),
    ("M15", G, '"SC101": 2,', '"SC101": 1,', "grade: zero-costs weight 2 -> 1"),
    ("M16", G, '(3, "D")', '(4, "D")', "grade: D band off-by-one"),
    ("M17", S, "return sr_annualized / math.sqrt(periods_per_year)",
     "return sr_annualized / periods_per_year", "units: de-annualize without sqrt"),
    ("M18", S, "trials_pp = [deannualize(s, periods_per_year) for s in trials]",
     "trials_pp = trials", "units: use annualized trial Sharpes as per-period"),
    ("M19", S, "z = (sr - sr_star) * math.sqrt(n - 1)", "z = (sr - sr_star) * math.sqrt(n)",
     "PSR: sqrt(n-1) -> sqrt(n)"),
    ("M20", S, "((kurtosis - 1.0) / 4.0)", "((kurtosis - 3.0) / 4.0)",
     "PSR: Pearson vs excess kurtosis mix-up"),
    ("M21", S, "var_pp = statistics.variance(trials_pp)", "var_pp = statistics.pvariance(trials_pp)",
     "DSR: sample -> population variance of trials"),
    ("M22", S, "sd = statistics.stdev(x)", "sd = statistics.pstdev(x)", "Sharpe: ddof 1 -> 0"),
    ("M23", S, "g * _NORMAL.inv_cdf(\n        1.0 - 1.0 / (n_trials * math.e)",
     "g * _NORMAL.inv_cdf(\n        1.0 - 1.0 / (n_trials)", "E[max]: drop the e in 1/(N e)"),
    ("M24", S, "return m3 / m2**1.5, m4 / m2**2", "return m3 / m2**1.5, m4 / m2**2 - 3.0",
     "moments: return excess kurtosis"),
    ("M25", V, "findings = placeholders + schema", "findings = schema",
     "validate: ignore placeholders"),
    ("M26", L, "        if self.check_event(AliasEvent):\n",
     "        if False and self.check_event(AliasEvent):\n", "loader: allow aliases"),
    ("M27", L, "                if duplicate:\n", "                if False:\n",
     "loader: allow duplicate keys"),
    ("M28", B, '"<": "&lt;", ', "", "badge: stop escaping <"),
    ("M29", F, "in_rows = [r for r in returns if r.day < split_date]",
     "in_rows = [r for r in returns if r.day <= split_date]", "fill: split day leaks into IS"),
    ("M30", F, '"fees_bps": PLACEHOLDER, ', '"fees_bps": 0, ', "fill: default fees to 0"),
    ("M31", R, 'if s["in_sample"] > 0 and', 'if s["in_sample"] >= 0 and',
     "SC105: in-sample Sharpe of exactly 0 counts"),
    ("M32", R, "if 0 < days < MIN_OOS_DAYS:", "if 0 <= days < MIN_OOS_DAYS:",
     "SC106: zero-length window also short"),
    ("M33", S, "if variance_term <= 0.0:", "if variance_term < 0.0:",
     "PSR: allow zero variance term"),
    ("M34", S, "    if sd == 0.0:\n", "    if False:\n", "Sharpe: drop zero-dispersion guard"),
    ("M35", S, "    if n_trials == 1:\n        return float(mean_sr)\n", "",
     "E[max]: drop the N = 1 branch"),
    ("M36", "cli.py", "ch if ch.isprintable() or ch == \" \" else", "ch if True else",
     "cli: stop escaping control characters"),
    ("M37", V, "if f.path not in placeholder_paths", "if True",
     "validate: report placeholder paths twice"),
    ("M38", R, 'if dsr is not None and dsr["value"] < DSR_THRESHOLD:',
     'if dsr is not None and dsr["value"] < DSR_THRESHOLD and dsr["n_trials"] > 1:',
     "SC104: skip single-trial DSR"),
    # ---- review round 1
    ("M39", F, '"trial_sharpe_variance_annualized": res.trial_sharpe_variance_annualized,',
     '"trial_sharpe_variance_annualized": res.trial_sharpe_variance_per_period,',
     "X17 fill: per-period trial variance in annualized field"),
    ("M40", F, '"sr_benchmark_annualized": res.sr_benchmark_annualized,',
     '"sr_benchmark_annualized": res.sr_benchmark_per_period,',
     "X18 fill: per-period SR* in annualized field"),
    ("M41", R, "NEGLIGIBLE_COST_BPS = 0.5", "NEGLIGIBLE_COST_BPS = 0.4", "SC101: threshold 0.5 -> 0.4"),
    ("M42", R, "SR_TOLERANCE = 0.01", "SR_TOLERANCE = 0.1", "SC007/SC009: tolerance x10"),
    ("M43", R, "DSR_TOLERANCE = 0.005", "DSR_TOLERANCE = 0.05", "SC008: tolerance x10"),
    ("M44", R, "NOBS_FACTOR = 1.2", "NOBS_FACTOR = 1.3", "SC010: factor 1.2 -> 1.3"),
    ("M45", R, '_recomputed_sr_star_pp(card, dsr),\n            dsr["n_observations"]',
     'stats.deannualize(dsr["sr_benchmark_annualized"], ppy),\n            dsr["n_observations"]',
     "SC008: trust the stated SR*"),
    ("M46", R, "    SC009,\n", "", "drop rule SC009 from RULES"),
    ("M47", R, "abs(stated - expected) > SR_TOLERANCE", "stated - expected > SR_TOLERANCE",
     "SC007: only flag overstated SR*"),
    ("M48", F, "PPY_MISMATCH_FACTOR = 1.15", "PPY_MISMATCH_FACTOR = 1.25", "fill: ppy factor 1.15 -> 1.25"),
    ("M49", F, "    check_periods_per_year(returns, periods_per_year)\n", "",
     "fill: skip the periods-per-year check"),
    ("M50", F, 'if line.lstrip().startswith("#"):\n            raise',
     'if False:\n            raise', "fill: allow comments after the header"),
    ("M51", F, "if None in rec:", "if False:", "fill: allow ragged rows"),
    ("M52", L, "if not stat.S_ISREG(os.fstat(fd).st_mode):", "if False:",
     "loader: read non-regular files"),
    ("M53", L, "os.chmod(tmp, mode)", "os.chmod(tmp, 0o644)", "loader: always 0644 on write"),
    ("M54", L, 'r"^[-+]?(?:0|[1-9][0-9]*)$"', 'r"^[-+]?[0-9]+$"', "loader: leading-zero ints"),
    ("M55", L, "except Exception as exc:  # noqa: BLE001",
     "except (ValueError, TypeError, OverflowError) as exc:  # noqa: BLE001",
     "loader: narrow except again"),
    ("M56", "cli.py", 'reconfigure(errors="backslashreplace")', 'reconfigure(errors="strict")',
     "cli: strict stdout encoding"),
    ("M57", "cli.py", "log_name = Path(args.trials).name", "log_name = args.trials",
     "cli: full trial-log path in card"),
    ("M58", "cli.py", 'b.add_argument("--force", action="store_true",',
     'b.add_argument("--force", action="store_true", default=True,',
     "cli: badge overwrites by default"),
    ("M59", B, '{"&": "&amp;", ', "{", "badge: stop escaping &"),
    ("M60", "schema/strategy-card-0.1.schema.json",
     '"trial_sharpe_variance_annualized",\n            "n_observations",',
     '"n_observations",', "schema: trial variance not required"),
    # ---- review round 2
    ("M61", F, "return (len(rows) - 1) / years", "return len(rows) / years", "ppy: fence-post n instead of n-1"),
    ("M62", F, "DAYS_PER_YEAR = 365.25", "DAYS_PER_YEAR = 365", "ppy: 365.25 -> 365 (A21)"),
    ("M63", R, "DAYS_PER_YEAR = 365.25", "DAYS_PER_YEAR = 365", "SC010: 365.25 -> 365"),
    ("M64", R, "NOBS_SLACK_PERIODS = 1.0", "NOBS_SLACK_PERIODS = 0.0", "SC010: drop one-period slack (A04)"),
    ("M65", R, 'intervals = dsr["n_observations"] - 1', 'intervals = dsr["n_observations"]', "SC010: fence-post n instead of n-1"),
    ("M66", F, "if given > WEEKDAYS_PER_YEAR and not any(", "if False and not any(", "ppy: drop weekend check"),
    ("M67", F, "WEEKDAYS_PER_YEAR = 262", "WEEKDAYS_PER_YEAR = 300", "ppy: weekend threshold 262 -> 300"),
    ("M68", R, "if g4 < g3 * g3 + 1 - 1e-9:", "if g4 < g3 * g3 - 1e-9:", "SC011: Pearson bound off by 1"),
    ("M69", R, "    if g4 > max_kurt + 1e-9:\n", "    if False:\n", "SC011: drop kurtosis maximum"),
    ("M70", R, "if var < MIN_TRIAL_VARIANCE:", "if var <= 0:", "SC012: epsilon -> exact zero"),
    ("M71", R, 'if dsr is None or dsr["n_trials"] <= 1:', 'if dsr is None or dsr["n_trials"] < 1:', "SC012: single trial also checked"),
    ("M72", R, "except (ValueError, ArithmeticError, StatisticsError) as exc:", "except ValueError as exc:", "rules: narrow arithmetic catch"),
    ("M73", F, "except (ValueError, ArithmeticError) as exc:  # StatisticsError is a ValueError", "except ValueError as exc:", "fill: narrow arithmetic catch"),
    ("M74", "schema/strategy-card-0.1.schema.json", '"maximum": 525960,', '"maximum": 5259600,', "schema: ppy maximum x10"),
    ("M75", "schema/strategy-card-0.1.schema.json", '"maximum": 1000,', '"maximum": 1e308,', "schema: Sharpe maximum removed"),
    ("M76", L, "os.link(tmp, p, follow_symlinks=False)", "os.replace(tmp, p)", "writer: non-exclusive publish (A20/A23)"),
    ("M77", L, "return 0o666 & ~mask", "return 0o644", "writer: ignore umask"),
    ("M78", F, "if not _ISO_DATE.fullmatch(raw):", "if False:", "dates: accept any fromisoformat"),
    ("M79", F, "        if dupes:\n", "        if False:\n", "csv: allow duplicate headers"),
    ("M80", R, "    SC011,\n", "", "drop rule SC011 from RULES"),
    ("M81", R, "    SC012,\n", "", "drop rule SC012 from RULES"),
]
# fmt: on


BACKUP_SUFFIX = ".mutbak"
TEST_TIMEOUT_S = 300


def sources() -> list[Path]:
    return sorted([*SRC.rglob("*.py"), *SRC.rglob("*.json")])


def digest() -> dict[str, str]:
    return {str(p.relative_to(SRC)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources()}


TIMEOUT = -1
# No -x: with -x pytest exits 1 even for a collection error, which would look like a kill.
PYTEST = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts="]


def run_tests() -> tuple[int, str]:
    try:
        r = subprocess.run(PYTEST, cwd=ROOT, capture_output=True, text=True, timeout=TEST_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return TIMEOUT, ""
    lines = [ln for ln in r.stdout.strip().splitlines() if ln.strip()]
    return r.returncode, lines[-1] if lines else ""


def classify(code: int, summary: str) -> str:
    """Killed only when tests RAN and FAILED: exit code 1 and a summary line that reports
    'N failed' and no 'error'. pytest exit codes: 0 passed, 1 failed, 2 interrupted or
    collection error, 3 internal, 4 usage, 5 no tests."""
    if code == TIMEOUT:
        return "TIMEOUT"
    if code == 0:
        return "SURVIVED"
    if code == 1 and re.search(r"\b\d+ failed\b", summary) and "error" not in summary:
        return "killed"
    return f"ERROR(rc={code})"


def _raise_interrupt(signum, frame):  # noqa: ARG001
    raise KeyboardInterrupt(f"signal {signum}")


def mutate_and_test(path: Path, old: str, new: str) -> tuple[int, str]:
    """Back up, mutate, test, restore. The backup survives only a SIGKILL."""
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    shutil.copy2(path, backup)
    try:
        original = backup.read_text()
        path.write_text(original.replace(old, new))
        return run_tests()
    finally:
        shutil.copy2(backup, path)
        backup.unlink()


def main() -> int:
    stale = sorted(SRC.rglob(f"*{BACKUP_SUFFIX}"))
    if stale:
        print("Stale mutation backups found. An earlier run did not finish. Restore them first:")
        for b in stale:
            print(f"  mv {b} {b.with_name(b.name.removesuffix(BACKUP_SUFFIX))}")
        return 3
    signal.signal(signal.SIGINT, _raise_interrupt)
    signal.signal(signal.SIGTERM, _raise_interrupt)
    before = digest()
    if run_tests()[0] != 0:
        print("baseline suite fails; fix it before mutation testing")
        return 2
    only = set(sys.argv[1:])
    rows = []
    for mid, fname, old, new, desc in MUTATIONS:
        if only and mid not in only:
            continue
        path = SRC / fname
        original = path.read_text()
        count = original.count(old)
        if count != 1:
            rows.append((mid, fname, desc, f"INVALID (snippet found {count}x)"))
            continue
        code, summary = mutate_and_test(path, old, new)
        rows.append((mid, fname, desc, classify(code, summary)))
    after = digest()

    width = max(len(r[2]) for r in rows)
    print(f"{'id':<4} {'file':<12} {'mutation':<{width}} result")
    for mid, fname, desc, result in rows:
        print(f"{mid:<4} {Path(fname).name[:12]:<12} {desc:<{width}} {result}")
    killed = sum(r[3] == "killed" for r in rows)
    print(f"\n{killed}/{len(rows)} killed")
    restored = before == after
    print("source restored byte-for-byte:", restored)
    for name in before:
        print(
            f"  {name:<40} {after.get(name, 'MISSING')[:16]}  {'ok' if before[name] == after.get(name) else 'CHANGED'}"
        )
    return 0 if restored and killed == len(rows) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\ninterrupted; the mutated file was restored from its .mutbak backup")
        raise SystemExit(130) from None
