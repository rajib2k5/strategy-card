# Validation rules

The validator runs in three stages. A later stage runs only if the earlier stages found no
problem.

1. **Input.** The tool reads and parses the file. A problem here gives `SC900` and exit code 2.
2. **Placeholders and schema.** The tool looks for the placeholder `__FILL_ME__` (`SC006`).
   Then it checks the card against the JSON Schema (`SC000`). A schema error at the path of a
   placeholder is not reported twice.
3. **Logic rules.** These run only on a schema-valid card that has no placeholders.

## A card is a disclosure, not a proof

The validator checks that a card is complete, consistent and possible. It cannot check that
the card is true. The author can choose every input below, and a forger who chooses them
consistently gets a passing card. Read every card with this in mind.

### Checked by the validator

If `performance.deflated_sharpe` is present, the schema requires all its inputs:
`sharpe_annualized`, `sr_benchmark_annualized`, `trial_sharpe_variance_annualized`,
`n_observations`, `skew` and `kurtosis_pearson`. Then:

- SC007 recomputes SR\* from `n_trials` and the trial variance.
- SC008 recomputes the DSR value from the inputs and the recomputed SR\*.
- SC009 requires the DSR Sharpe to equal the in-sample Sharpe.
- SC010 requires `n_observations` to agree with the in-sample window and `periods_per_year`.
- SC011 requires the skew and kurtosis to be possible for `n_observations` points.
- SC012 requires a trial variance above 1e-12 when there is more than one trial.

### Taken on trust

The validator cannot verify these inputs. It only checks the limits shown.

| Input | What the validator checks | What it cannot check |
|---|---|---|
| The Sharpe ratios (`performance.sharpe`, `deflated_sharpe.sharpe_annualized`) | finite, \|SR\| ≤ 1000, DSR Sharpe = in-sample Sharpe | that they come from real returns |
| The window dates | valid dates, start < end, out-of-sample after in-sample | that the backtest used these dates |
| `periods_per_year` | > 0 and ≤ 525,960; consistent with `n_observations` and the window (SC010) | the real sampling frequency. `fill` checks it against the CSV dates; a hand-written card is not checked |
| The trial count (`trials.count`, `n_trials`) | 1 to 10,000,000; the two values agree | that no other variants were tried |
| The trial variance | > 1e-12 when there is more than one trial | its real value. A small variance gives a small SR\* and a high DSR |
| `n_observations` | 2 to 100,000,000; within the SC010 band | the real count inside that band |
| Skew and kurtosis | possible for `n_observations` points (SC011) | their real values. Feasible but false moments pass (see fixture `warn/SC104__forged-feasible-moments.yaml`) |
| Costs, point-in-time, survivorship, sources | the declared values only | that they are true |

## Constants

| Constant | Value | Unit | Rule |
|---|---|---|---|
| SR tolerance | 0.01 | annualized Sharpe | SC007, SC009 |
| DSR tolerance | 0.005 | probability | SC008 |
| Observation factor | 1.2 | ratio | SC010 |
| Observation slack | 1 | period | SC010 |
| Days per year | 365.25 | days | SC010 |
| Minimum trial variance | 1e-12 | annualized Sharpe² | SC012 |
| Negligible cost | 0.5 | bps, fees + slippage | SC101 |

The tolerances allow for rounding to four decimal places.

SC010 in detail: n rows span n − 1 periods. Let P = (in-sample end − start in days) /
365.25 × `periods_per_year`. The rule passes when P / 1.2 − 1 ≤ n_observations − 1 ≤
P × 1.2 + 1. Two monthly or quarterly rows therefore pass.

## Schema maxima

| Field | Maximum | Reason |
|---|---|---|
| `trials.count`, `deflated_sharpe.n_trials` | 10,000,000 | Larger values overflow the normal quantile used for SR\* |
| `deflated_sharpe.n_observations` | 100,000,000 | More than 190 years of one-minute bars |
| `performance.periods_per_year` | 525,960 | One period per minute of a 365.25-day year |
| Every annualized Sharpe field | ±1000 | Prevents float overflow in SR²; no real strategy is near it |

## Arithmetic errors

If a rule cannot be evaluated because of an arithmetic error (overflow, domain error),
the validator reports that rule as an error with the message `cannot evaluate`. It does
not show a traceback.

Rule IDs are stable. The project never renumbers or reuses an ID.

## Rule table

| ID | Name | Severity | Badge flag | The rule fires when |
|---|---|---|---|---|
| SC900 | input-invalid | input (exit 2) | — | The file is missing, too large, not UTF-8, not valid YAML, empty, not a mapping at the top level, has a duplicate key, or uses a YAML alias. |
| SC000 | schema | error | — | The card does not match `strategy-card-0.1.schema.json`: a missing field, a wrong type, a bad enum value, an unknown key, or a bad date. |
| SC001 | window-order | error | — | For the in-sample or the out-of-sample window, `start` is not before `end`. |
| SC002 | oos-after-is | error | — | `out_of_sample.start` is on or before `in_sample.end`. The windows must not overlap. |
| SC003 | dsr-required | error | — | `trials.count` is more than 1 and `performance.deflated_sharpe` is missing. |
| SC004 | dsr-trials-mismatch | error | — | `deflated_sharpe.n_trials` is not equal to `trials.count`. |
| SC005 | non-finite-number | error | — | A number is NaN or infinite (`.nan`, `.inf` in YAML). JSON Schema `minimum` does not catch NaN. |
| SC006 | unfilled-placeholder | error | — | A value contains `__FILL_ME__`. `strategy-card fill` writes this placeholder for the fields that only a human can supply. |
| SC007 | dsr-benchmark-mismatch | error | — | The stated `sr_benchmark_annualized` differs by more than 0.01 from SR\* recomputed from `n_trials` and `trial_sharpe_variance_annualized`. |
| SC008 | dsr-value-mismatch | error | — | The stated DSR `value` differs by more than 0.005 from the DSR recomputed from the card's inputs, or the inputs are impossible (PSR variance term not positive). The recomputation uses the recomputed SR\*, not the stated one. |
| SC009 | dsr-sharpe-mismatch | error | — | `deflated_sharpe.sharpe_annualized` differs by more than 0.01 from `performance.sharpe.in_sample`. |
| SC010 | dsr-observations-implausible | error | — | `n_observations − 1` is outside [P / 1.2 − 1, P × 1.2 + 1], where P is the number of periods in the in-sample window (see "Constants"). |
| SC011 | dsr-moments-infeasible | error | — | Kurtosis < skew² + 1 (Pearson's bound), or \|skew\| > (n − 2) / √(n − 1), or kurtosis > (n² − 3n + 3) / (n − 1), with n = `n_observations`. |
| SC012 | dsr-trial-variance-zero | error | — | `n_trials` > 1 and `trial_sharpe_variance_annualized` < 1e-12. Distinct trials do not all have the same Sharpe ratio, and a zero variance makes SR\* zero. |
| SC101 | zero-costs | warning | `zero-costs` | `fees_bps + slippage_bps` is less than 0.5 bps (negligible costs). A total of exactly 0.5 does not fire. |
| SC102 | not-point-in-time | warning | `not-pit` | `data.point_in_time` is `restated`, `unknown` or `mixed`. (`mixed` is included because part of the data is restated.) |
| SC103 | survivorship-bias | warning | `survivor-bias` | `data.survivorship.handling` is `survivor_only` or `unknown`. |
| SC104 | low-dsr | warning | `low-dsr` | `deflated_sharpe.value` is less than 0.95. A value of exactly 0.95 does not fire. |
| SC105 | oos-decay | warning | `oos-decay` | The in-sample Sharpe is more than 0 and the out-of-sample Sharpe is less than 0.5 × the in-sample Sharpe. This is a heuristic. |
| SC106 | short-oos | warning | `short-oos` | The out-of-sample window is shorter than 365 calendar days. |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | No errors. Warnings are allowed. |
| 1 | One or more errors. With `--strict`, one or more warnings also give 1. |
| 2 | The input is unreadable or invalid (`SC900`). |

## Grade

The grade is a deterministic function of the findings.

- If there is one or more errors, the grade is **F**.
- If there are no errors, each warning rule that fires adds its weight one time. A rule that
  fires two times still adds its weight one time.

| Warning | Weight |
|---|---|
| SC101 zero-costs | 2 |
| SC102 not-point-in-time | 1 |
| SC103 survivorship-bias | 1 |
| SC104 low-dsr | 2 |
| SC105 oos-decay | 1 |
| SC106 short-oos | 1 |

| Points | Grade |
|---|---|
| 0 | A |
| 1 | B |
| 2 | C |
| 3–4 | D |
| 5 or more | E |
| any error | F |

The badge shows the grade and the first two flags in rule order. If more flags fire, the badge
adds `+N`. An F badge shows the number of errors.

The grade measures the disclosure. It does not measure the quality of the strategy. An A card
can describe a strategy that loses money.
