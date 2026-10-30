"""Command-line interface. Analysis only: no orders, no brokers, no network."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .badge import render
from .fill import build_card, dump_card, parse_iso_date, read_returns, read_trials
from .loader import InputError, write_text_atomic
from .validate import EXIT_INPUT, EXIT_OK, Report, schema_text, validate_file


def _safe(text: str) -> str:
    """Escape control characters so card content cannot drive the terminal."""
    return "".join(
        ch if ch.isprintable() or ch == " " else ch.encode("unicode_escape").decode("ascii")
        for ch in text
    )


def _print_text(report: Report, strict: bool) -> None:
    out = sys.stdout
    if report.input_error:
        out.write(f"{_safe(report.file)}: cannot validate\n")
    else:
        g = report.grade
        assert g is not None
        out.write(
            f"{_safe(report.file)}: grade {g.letter} "
            f"({len(report.errors)} error(s), {len(report.warnings)} warning(s))\n"
        )
    for f in report.findings:
        out.write(f"  {f.severity:<7} {f.id} {f.name} at {_safe(f.path)}: {_safe(f.message)}\n")
    code = report.exit_code(strict)
    if strict and code == 1 and not report.errors:
        out.write("  --strict: warnings are treated as failures\n")


def cmd_validate(args: argparse.Namespace) -> int:
    report = validate_file(args.card)
    if args.format == "json":
        sys.stdout.write(json.dumps(report.as_dict(args.strict), indent=2) + "\n")
    else:
        _print_text(report, args.strict)
    return report.exit_code(args.strict)


def cmd_badge(args: argparse.Namespace) -> int:
    report = validate_file(args.card)
    if report.input_error:
        _print_text(report, False)
        return EXIT_INPUT
    g = report.grade
    assert g is not None
    out = Path(args.output)
    try:
        write_text_atomic(out, render(g), overwrite=args.force)
    except FileExistsError:
        sys.stderr.write(f"{_safe(str(out))} exists; pass --force to overwrite it\n")
        return EXIT_INPUT
    except OSError as exc:
        sys.stderr.write(f"cannot write {_safe(str(args.output))}: {_safe(str(exc))}\n")
        return EXIT_INPUT
    sys.stdout.write(f"wrote {_safe(str(args.output))} (grade {g.letter})\n")
    return EXIT_OK


def cmd_fill(args: argparse.Namespace) -> int:
    out = Path(args.output)
    try:
        split = parse_iso_date(args.split_date) if args.split_date else None
    except ValueError:
        sys.stderr.write("--split-date must be a YYYY-MM-DD date\n")
        return EXIT_INPUT
    try:
        returns = read_returns(args.returns)
        trials, unit = read_trials(args.trials)
        # Only the file name goes into the card: a full path can leak a home directory.
        log_name = Path(args.trials).name
        card = build_card(returns, trials, unit, args.periods_per_year, split, log_name)
        write_text_atomic(out, dump_card(card), overwrite=args.force)
    except FileExistsError:
        sys.stderr.write(f"{_safe(str(out))} exists; pass --force to overwrite it\n")
        return EXIT_INPUT
    except InputError as exc:
        sys.stderr.write(f"error: {_safe(str(exc))}\n")
        return EXIT_INPUT
    except OSError as exc:
        sys.stderr.write(f"cannot write {_safe(str(out))}: {_safe(str(exc))}\n")
        return EXIT_INPUT
    sys.stdout.write(
        f"wrote {_safe(str(out))}: {len(trials)} trial(s), {len(returns)} return(s). "
        "Fill every __FILL_ME__, then run `strategy-card validate`.\n"
    )
    return EXIT_OK


def cmd_schema(args: argparse.Namespace) -> int:
    sys.stdout.write(schema_text())
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="strategy-card",
        description="Disclosure card and badge for a backtest. "
        "Analysis only; not investment advice.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="check a card against the schema and the rules")
    v.add_argument("card")
    v.add_argument("--strict", action="store_true", help="treat warnings as failures")
    v.add_argument("--format", choices=("text", "json"), default="text")
    v.set_defaults(func=cmd_validate)

    b = sub.add_parser("badge", help="write an SVG badge with the grade of a card")
    b.add_argument("card")
    b.add_argument("-o", "--output", required=True)
    b.add_argument("--force", action="store_true", help="overwrite an existing output file")
    b.set_defaults(func=cmd_badge)

    f = sub.add_parser("fill", help="draft a card from a returns CSV and a trial-log CSV")
    f.add_argument("--returns", required=True, help="CSV with columns date,return")
    f.add_argument(
        "--trials",
        required=True,
        help="CSV, one row per variant tried, with a sharpe_annualized or sharpe_per_period column",
    )
    f.add_argument(
        "--periods-per-year",
        type=float,
        required=True,
        help="periods per year of the returns (252 daily, 52 weekly, 12 monthly); "
        "checked against the median date gap",
    )
    f.add_argument("--split-date", help="first out-of-sample date (YYYY-MM-DD)")
    f.add_argument("-o", "--output", required=True)
    f.add_argument("--force", action="store_true", help="overwrite an existing output file")
    f.set_defaults(func=cmd_fill)

    s = sub.add_parser("schema", help="print the JSON Schema of the card")
    s.set_defaults(func=cmd_schema)
    return p


def _harden_streams() -> None:
    """Never crash on a terminal that cannot encode card text (e.g. PYTHONIOENCODING=ascii)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="backslashreplace")
            except (ValueError, OSError):
                pass


def main(argv: list[str] | None = None) -> int:
    _harden_streams()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
