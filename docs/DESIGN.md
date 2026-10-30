# strategy-card v0.1 — design

## Problem

A backtest result says almost nothing without the context that produced it: how many
variants were tried, which costs were charged, whether the data was point-in-time, and
whether delisted names were in the universe. That context is usually missing, or it sits
in prose that no tool can check.

strategy-card is a one-page YAML disclosure card for a backtest, a JSON Schema for it, a
validator with stable rule IDs, a deterministic A–F grade, and a static SVG badge. A
filler builds most of a card from a returns CSV and a trial-log CSV and computes the
Deflated Sharpe Ratio (DSR).

## Constraints

- **Analysis only.** No order execution, no broker code, no network calls. The tool reads
  local files and writes local files.
- **Small dependency surface.** Runtime deps are `pyyaml` and `jsonschema` only. Statistics
  use the stdlib (`statistics`, `math`, `statistics.NormalDist`). numpy/scipy are test-only,
  as an independent oracle.
- **Python >= 3.10.** CI runs 3.10 and 3.12.
- **Untrusted input.** A card or CSV can come from anyone (a PR, a paper's supplement). See
  `SECURITY.md`.
- **Deterministic output.** Same card in → same findings, same grade, same SVG bytes out.
- **Stable rule IDs.** Users will pin `SC0xx` IDs in CI and in discussion. IDs are never
  reused or renumbered; a retired rule keeps its ID reserved.

## A card is a disclosure, not a proof

The validator makes omissions and internal contradictions visible. It cannot detect a
consistent lie. These inputs are taken on trust (details and limits in `RULES.md`):

- the Sharpe ratios themselves;
- the window dates;
- the trial count;
- the trial variance, beyond being > 0 when there is more than one trial;
- the skew and kurtosis, beyond necessary bounds (Pearson and sample-size). Passing SC011
  does not prove that the moments are achievable;
- the stated SR\* and DSR inside the 1e-4 tolerance (SC104 grades the lower DSR);
- `n_observations` and `periods_per_year` inside the SC010 band;
- costs, point-in-time status, survivorship handling and sources.

A forged card that picks these values consistently passes. An earlier version of this
document said that only the trial variance was taken on trust. That was false.

## Non-goals

- Running backtests, fetching prices, or judging whether a strategy is good. The card
  grades the *disclosure*, not the alpha.
- Proving that a card is honest. A card can lie; the validator only makes omissions and
  internally inconsistent claims visible.
- Hosting badges. The badge is a static file the user commits.
- Investment advice of any kind.

## Decisions (fixed for v0.1)

| Decision | Choice |
|---|---|
| Card format | YAML, `schema_version: "0.1"`, validated by a draft 2020-12 JSON Schema shipped as package data (`strategy-card schema` prints it) |
| Strictness | `additionalProperties: false` on every object, so a typo is an error, not a silently ignored key |
| Validation order | input checks (exit 2) → placeholder scan + schema (SC000/SC006) → logic rules. Logic rules run only on a schema-valid card with no placeholders, so every rule can assume correct types |
| Rules | one module (`rules.py`), each rule a small pure function `card -> list[Finding]`, registered in one tuple. Severity is `error` or `warning`. Catalogue in `RULES.md` |
| Exit codes | 0 ok (warnings allowed) · 1 errors, or warnings under `--strict` · 2 unreadable or invalid input |
| Grade | deterministic function of the findings: any error → F; otherwise warning weights sum to a letter A–E (see `RULES.md`) |
| Badge | shields.io "flat" style SVG, label `strategy card`, message = grade + up to two flags. No fonts, scripts, links or external references. Refuses to overwrite without `--force` |
| DSR checks | the card's DSR is recomputed from its own inputs (SC007, SC008), cross-checked against the in-sample Sharpe (SC009) and the in-sample window (SC010), its moments must pass necessary bounds (SC011; passing does not prove they are achievable), and identical trials warn (SC012). Many inputs remain taken on trust: see "A card is a disclosure, not a proof" below and `RULES.md` |
| Periods per year | `fill --periods-per-year` is required and checked against (rows − 1) / calendar years spanned, factor 1.15. A value above 262 needs weekend dates in the data |
| Filler | computes Sharpe, trial count and DSR from CSVs, rounds them to 4 dp (the trial variance to 6 significant figures) and recomputes SR\* and the DSR from the rounded inputs. Fields that CSVs cannot know are written as the placeholder `__FILL_ME__`, which fails validation (SC006) until a human replaces it. Before writing, it validates the draft with neutral stand-ins for the human fields and refuses (exit 2) if any other error appears |
| DSR units | every Sharpe ratio that enters PSR / E[max SR] / DSR is **per-period**. The trial log must name its unit in the header (`sharpe_annualized` or `sharpe_per_period`), so the unit is never guessed. The card stores annualized values for display |
| DSR null | SR\* = expected maximum of N trial Sharpes under a zero-mean null (Bailey & López de Prado 2014, eq. for SR0), using the sample variance (ddof = 1) of the trial Sharpes. Skew and **Pearson** kurtosis (normal = 3) of the chosen strategy's returns, moment estimators (bias = True) |

## Alternatives rejected

1. **Put the logic rules into the JSON Schema** (`if/then`, `dependentRequired`, custom
   keywords). Rejected: draft 2020-12 cannot compare two dates or two numbers in a portable
   way, and schema error messages do not carry stable IDs or severities. Warnings do not
   exist in JSON Schema at all. The schema checks shape; Python checks meaning.
2. **Use numpy/scipy/pandas at runtime for the statistics.** Rejected: three heavy
   dependencies for one mean, one stdev, four moments and two normal-quantile calls.
   `statistics.NormalDist` gives `cdf` and `inv_cdf` to double precision. scipy stays as a
   test-only oracle, which is also a better test: the shipped code and the oracle do not
   share an implementation.
3. *(Also considered)* **Let the filler default unknown fields to "reasonable" values**
   (fees 0, `point_in_time: unknown`). Rejected: a default is a silent claim. Zero costs
   would look like a disclosure. A placeholder that fails validation forces a human decision.

## Failure modes

| Failure | Handling |
|---|---|
| Malformed YAML, non-UTF-8, empty file, top level not a mapping | exit 2, finding `SC900` |
| Input larger than the cap (1 MiB card, 20 MiB CSV) | exit 2, file is not parsed |
| YAML aliases / anchors (billion laughs), duplicate keys | exit 2. Aliases are refused; a duplicate key would silently hide a value |
| YAML timestamps auto-converted to `date` objects | the loader keeps all scalars as strings or numbers; dates are checked by the schema `format: date` and pattern |
| NaN / Inf in a numeric field (passes JSON Schema `minimum`) | rule SC005, error |
| Unit mix-up between annualized and per-period Sharpe | unit named in the CSV header; tests pin both paths to the same DSR |
| Wrong `--periods-per-year` (e.g. 252 for monthly data, 365 for equity daily data) | exit 2 with the inferred value |
| Extreme numbers (1e308, 10**16 trials) | schema maxima (SC000); any remaining arithmetic error in a rule is reported as that rule's error, never a traceback |
| Output file appears between a check and the write | no separate check: `os.link` publish fails with EEXIST without `--force` |
| A card states a DSR its inputs do not support | SC007/SC008 errors |
| `#` line hides a losing trial | exit 2; comments only before the header |
| FIFO or device as input | exit 2 without blocking |
| Trial log with one trial | no DSR is computed (variance undefined); `trials.count = 1` does not require one |
| Zero-variance returns | Sharpe undefined → filler exits 2 with a message, never writes NaN into a card |
| `fill -o` would overwrite a hand-edited card | refused unless `--force` |
| Output path is a symlink | the writer writes a temp file in the same directory and `os.replace`s it, so it replaces the link, not the link target |
| Card claims are false | out of scope — see non-goals |

## Interface contract

- CLI: `strategy-card validate|badge|fill|schema` (see README).
- `--format json` output of `validate`: `{"file", "exit_code", "grade", "findings": [{"id",
  "name", "severity", "path", "message", "flag"}]}`. Field set is stable for 0.x.
- Python API (not yet stable): `strategy_card.validate.validate_file`,
  `strategy_card.stats.deflated_sharpe_from_returns`.

## Increments

v0.1 ships schema 0.1. A schema change bumps `schema_version`; the validator will accept the
versions it knows and reject others with SC000. Rule IDs only grow.
