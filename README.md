# strategy-card

A one-page disclosure card for a backtest, with a validator and a badge. It is like a model card, but for trading strategies.

![strategy card badge](docs/badge-demo.svg)

> **Analysis only. Not investment advice.** This tool reads files and writes files. It places no orders. It connects to no broker. It uses no network.

## What it does

A Sharpe ratio alone tells you little. The reader also needs to know these facts:

- How many variants of the strategy did the author try?
- Which trading costs did the backtest charge?
- Was the data point-in-time, or was it restated later?
- Did the universe keep the companies that were delisted?

A strategy card records these facts in one YAML file. The tool then does four jobs:

1. `validate` checks the card against a JSON Schema and against logic rules. Each rule has a stable ID, for example `SC002`.
2. `badge` gives the card a grade from A to F and writes an SVG badge.
3. `fill` drafts a card from a returns CSV and a trial-log CSV. It calculates the Sharpe ratio and the Deflated Sharpe Ratio (DSR).
4. `schema` prints the JSON Schema.

The grade measures the disclosure. It does not measure the quality of the strategy.

## Install

```
pip install git+https://github.com/rajib2k5/strategy-card
```

This line works when the GitHub repository is reachable.

For development, clone the repository and install it in editable mode:

```
pip install -e ".[dev]"
pytest
```

The tool needs Python 3.10 or later. The runtime dependencies are `pyyaml` and `jsonschema` only.

## Sample card

This card is in [`examples/sample-card.yaml`](examples/sample-card.yaml). All the numbers come from synthetic data.

```yaml
schema_version: "0.1"
strategy:
  name: Synthetic momentum (demo)
  description: >-
    Demo card built from seeded synthetic returns. It shows the card format only and
    describes no real strategy.
test_window:
  in_sample:
    start: "2015-01-02"
    end: "2021-12-31"
  out_of_sample:
    start: "2022-01-03"
    end: "2024-08-29"
costs:
  fees_bps: 1.0
  slippage_bps: 2.0
  model: Per side, on traded notional, charged at every rebalance.
trials:
  count: 25
  log: examples/trials.csv
performance:
  periods_per_year: 252
  sharpe:
    in_sample: 1.2106
    out_of_sample: 0.5120
  deflated_sharpe:
    value: 0.9426
    n_trials: 25
    sharpe_annualized: 1.2106
    sr_benchmark_annualized: 0.6208
    trial_sharpe_variance_annualized: 0.0966249
    n_observations: 1826
    skew: -0.1333
    kurtosis_pearson: 3.0906
    method: >-
      Bailey & Lopez de Prado (2014): PSR at SR* = E[max SR] over n_trials, zero-mean
      null, sample variance of trial Sharpes, in-sample returns
data:
  point_in_time: as_reported
  survivorship:
    handling: not_applicable
    notes: Synthetic single-series data. There is no universe, so no names can be delisted.
  sources:
    - name: Synthetic returns from scripts/make_synthetic.py (seed 20260101)
      license: MIT
```

This card gets grade D. The DSR is 0.9426, which is less than 0.95 (`SC104`). The out-of-sample Sharpe ratio is less than half of the in-sample Sharpe ratio (`SC105`).

## Usage

### Validate a card

```
strategy-card validate card.yaml
strategy-card validate card.yaml --strict
strategy-card validate card.yaml --format json
```

| Exit code | Meaning |
|---|---|
| 0 | The card has no errors. It can have warnings. |
| 1 | The card has one or more errors. With `--strict`, warnings also give exit code 1. |
| 2 | The tool cannot read the file, or the file is not valid YAML. |

### Make a badge

```
strategy-card badge card.yaml -o badge.svg
```

If `badge.svg` exists, the command stops. Use `--force` to overwrite it. An overwritten file keeps its permissions.

The badge is a static SVG file. It has no scripts, no links and no external fonts. Commit it to your repository and show it in your README.

### Draft a card from CSV files

```
strategy-card fill --returns returns.csv --trials trials.csv \
  --periods-per-year 252 --split-date 2022-01-03 -o card.yaml
```

- `returns.csv` has the columns `date,return`. Each return is a per-period simple excess return.
- `trials.csv` has one row for each variant that you tried. It must have a column `sharpe_annualized` or a column `sharpe_per_period`. The column name tells the tool the unit, so the tool never guesses it.
- `--periods-per-year` is required: 252 for daily trading data, 52 for weekly, 12 for monthly. The tool also calculates (rows − 1) / calendar years from the dates. If your value and that value differ by more than a factor of 1.15, the tool stops with exit code 2. A value above 262 (for example 365) needs weekend dates in the data.
- Dates must be in the format `YYYY-MM-DD`. A CSV header must not repeat a column name.
- `--split-date` is the first out-of-sample date. If you give it, the tool calculates the in-sample and the out-of-sample Sharpe ratios. The DSR uses the in-sample returns.
- Lines that start with `#` are comments. They are allowed only before the header row. A `#` line after the header is an error, because a hidden trial changes the DSR. A row with more fields than the header is also an error.
- The card stores only the file name of the trial log, not its full path.

The CSV files cannot tell the tool your strategy name, your costs, your data sources or your survivorship handling. The tool writes `__FILL_ME__` in these fields. If you do not give `--split-date`, the out-of-sample fields also get `__FILL_ME__`. The draft card fails validation with rule `SC006` until you replace every placeholder. The tool does not put a default value in a field that it cannot calculate from the CSV files, because a default is a claim that nobody made.

If `card.yaml` exists, `fill` stops. Use `--force` to overwrite it. `badge` works in the same way.

Try it with the synthetic examples:

```
strategy-card fill --returns examples/returns.csv --trials examples/trials.csv \
  --periods-per-year 252 --split-date 2022-01-03 -o my-card.yaml
strategy-card validate my-card.yaml
```

### Print the schema

```
strategy-card schema
```

## Rules

The full rule table, the exit codes and the grading method are in [docs/RULES.md](docs/RULES.md).

| Errors | Warnings |
|---|---|
| `SC000` schema, `SC001` window order, `SC002` out-of-sample after in-sample, `SC003` DSR required, `SC004` DSR trial count, `SC005` non-finite number, `SC006` placeholder, `SC007` DSR benchmark recomputed, `SC008` DSR value recomputed, `SC009` DSR Sharpe equals in-sample Sharpe, `SC010` DSR observations match the window, `SC011` skew and kurtosis inside necessary bounds | `SC101` costs below 0.5 bps, `SC102` not point-in-time, `SC103` survivorship bias, `SC104` DSR below 0.95, `SC105` out-of-sample decay, `SC106` short out-of-sample window, `SC012` identical trials |

`SC900` means that the tool cannot read the input.

## The Deflated Sharpe Ratio

If you try many variants and keep the best one, the best Sharpe ratio is high by chance. The DSR (Bailey and López de Prado, 2014) corrects for this. It gives the probability that the true Sharpe ratio is more than the expected maximum Sharpe ratio of N trials with no skill.

The tool calculates every Sharpe ratio per period before it uses the DSR formulas. It converts annualized trial Sharpe ratios with `SR / sqrt(periods_per_year)`. The kurtosis is Pearson kurtosis (3 for a normal distribution). The tests compare the result with the worked example in the paper (DSR ≈ 0.9004) and with a separate numpy/scipy implementation.

## Claude Code plugin

This repository is also a Claude Code plugin. The plugin has one skill, `strategy-card-fill`. The skill runs `strategy-card fill` and `strategy-card validate`. Then it asks you for the fields that only a person can supply.

The skill is for analysis only. It can use only file-read tools and the `fill`, `validate` and `badge` subcommands. It uses no network and places no orders. The skill text tells Claude not to pass `--force` without your agreement. This is an instruction in prose, not an enforced control: the allowed-tools pattern still permits `--force`. The tool itself refuses to overwrite a file without `--force`.

To install the plugin:

1. Install the package, so that the `strategy-card` command is on your `PATH`.
2. In Claude Code, add the marketplace and install the plugin:

   ```
   /plugin marketplace add rajib2k5/strategy-card
   /plugin install strategy-card@strategy-card
   ```

   On Claude Code 2.1.275 or later, one step is sufficient:

   ```
   /plugin install strategy-card --marketplace rajib2k5/strategy-card
   ```

## Design and security

- [docs/DESIGN.md](docs/DESIGN.md): constraints, non-goals, rejected alternatives, failure modes.
- [docs/SECURITY.md](docs/SECURITY.md): threat model for the YAML, CSV and SVG handling.

## What a card can and cannot prove

A card is a disclosure, not a proof. The validator checks that a card is complete, consistent, and inside necessary bounds. It cannot check that the numbers are true. For the skew and kurtosis, it checks necessary bounds (Pearson and sample-size) only. Passing these bounds does not prove that the moments are achievable. The Sharpe ratios, the window dates, the trial count, the trial variance and the moments are taken on trust, inside the limits in [docs/RULES.md](docs/RULES.md#taken-on-trust).

## Disclaimer

This tool is for analysis only. It is not investment advice. A good grade does not mean that a strategy will make money. A card can contain false claims; the validator checks only that the card is complete and consistent.

## License

MIT. Copyright 2026 Rajib Bahar. See [LICENSE](LICENSE).
