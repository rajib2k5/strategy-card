# Security notes (v0.1)

strategy-card reads files that other people write: YAML cards, returns CSVs and trial-log
CSVs. It writes YAML and SVG files. It has no network code, no subprocess calls and no
order-placement code. A badge SVG is often shown on a web page, so the SVG is also a trust
boundary.

## Trust boundaries

| Surface | Who controls it | What they can try |
|---|---|---|
| Card YAML (`validate`, `badge`) | Any author of a card, for example a pull request | Code execution through YAML tags, memory or CPU exhaustion, crashes, values that hide other values, terminal escape codes in output |
| Returns / trials CSV (`fill`) | The person who exported the backtest | Huge files or fields, non-numeric or non-finite values, unit confusion |
| SVG badge output | Shown in browsers and on GitHub | Script or markup injection through text in the badge |
| Output paths (`-o`) | The local user | Overwrite of a hand-edited card; writes through a symlink |
| Claude Code skill | Content of files the skill reads | Prompt injection through card or CSV text |

## Findings and controls

Each control has a test. Confidence is high for the items marked *tested*.

1. **YAML object construction.** Only a subclass of `yaml.SafeLoader` is used. `!!python/*`
   tags are refused. *Tested* (`test_python_tags_are_refused`).
2. **Alias bomb ("billion laughs").** All YAML aliases are refused, so expansion cannot
   happen. Real cards do not need aliases. *Tested* (alias and merge-key cases, plus fixture
   `SC900__alias-bomb.yaml`).
3. **Input size.** Cards are capped at 1 MiB, CSVs at 20 MiB. The reader reads at most
   cap + 1 bytes, so `/dev/zero` or a huge file is refused without full read. *Tested*.
4. **Crashes on hostile scalars (fixed in two rounds).** `!!int xyz`, `!!float xyz`, a bad
   `!!timestamp`, and integers with more than 4300 digits raised `ValueError` outside
   `yaml.YAMLError`. The first fix caught only `ValueError`, `TypeError` and `OverflowError`.
   The review then found `!!int ""` (`IndexError`) and `!!timestamp zz` (`AttributeError`).
   The loader now maps *any* exception during loading to `SC900`. *Tested*, fixtures
   `SC900__int-tag-empty.yaml` and `SC900__timestamp-tag-garbage.yaml`.
5. **Oversized CSV field (fixed during this review).** A field larger than
   `csv.field_size_limit()` raised `csv.Error` and crashed `fill`. It now gives exit 2.
   *Tested*, RED observed before the fix.
6. **Duplicate keys.** PyYAML keeps the last value of a duplicate key without a warning. An
   author could show `fees_bps: 5` near the top and override it with `0` lower down. The loader
   refuses duplicate keys. *Tested*.
7. **NaN and infinity.** JSON Schema `minimum: 0` does not reject NaN, because every
   comparison with NaN is false. Rule `SC005` rejects non-finite numbers. CSV readers reject
   them too. *Tested*.
8. **SVG injection.** All text in the SVG goes through an XML escape for `& < > " '`. The
   SVG has no `<script>`, no `href`, no `<image>`, no `<foreignObject>`, no external font and
   no URL except the SVG namespace. A property test with arbitrary labels parses the output
   with an XML parser. In v0.1 the badge text comes only from a fixed vocabulary (grade
   letters and rule flags), so card content never reaches the SVG. *Tested*.
9. **Terminal escape injection.** Card keys and values appear in error messages. Text
   output escapes non-printable characters, so a card cannot send ANSI or OSC sequences to
   the terminal. JSON output is escaped by `json.dumps`. *Tested*.
10. **Path handling.** No path inside a card is opened. `trials.log` is display text only,
    and `fill` writes only the file name of the trial log, so a home directory does not leak
    into a published card. Output files are written to a temp file in the same directory.
    Without `--force`, the temp file is published with `os.link`, which fails with EEXIST if
    anything is at the path, including a dangling symlink. There is no separate exists-check,
    so there is no check-then-write race (round 1 had one). With `--force`, `os.replace` is
    used: a symlink is replaced, its target is never written, and an existing regular file
    keeps its mode. New files get 0666 minus the umask (round 1 hard-coded 0644).
    *Tested*, including a file that appears after the temp file is created.
11. **No network, no shell.** After the package loads, `socket`, `urllib.request` and
    `http.client` are not in `sys.modules`; a test checks this in a fresh interpreter.
    `xml.sax.saxutils` was removed for this reason: it imports `urllib.request`. The package
    source imports no `subprocess`, `socket`, `urllib` or `http` module and calls no
    `os.system`, `os.popen`, `eval` or `exec`; an AST test checks this. (On Python 3.10,
    `jsonschema` imports the stdlib `uuid` module, which imports `subprocess`. The package
    never calls it.) *Tested*.
12. **Non-regular input files.** `validate` on a FIFO blocked forever. Inputs are now opened
    with `O_NONBLOCK` and refused with exit 2 unless `fstat` shows a regular file. This
    applies to cards and to both CSVs. *Tested* (FIFO card and FIFO CSV).
13. **Output encoding.** With `PYTHONIOENCODING=ascii`, Cyrillic card text crashed the CLI with
    `UnicodeEncodeError`. stdout and stderr now use `errors="backslashreplace"`. *Tested*.
14. **YAML 1.1 coercions.** `count: 031` loaded as 25 (octal) and `1:00.0` as 60.0 (base 60);
    `yes`/`no` loaded as booleans. Ints, floats and booleans now resolve like YAML 1.2,
    decimal only, so these stay strings and the schema rejects them. *Tested*.
15. **Hidden trials.** `#` lines anywhere in a CSV were dropped, so commenting out losing
    trials lowered N and raised the DSR. `#` lines are now allowed only before the header,
    and a row with more fields than the header is an error. *Tested*.
16. **Self-reported DSR.** The DSR block was trusted. A card with SR 0.3, SR\* 4.0, 10
    observations and DSR 0.999 got grade A under `--strict`. The inputs are now required,
    and rules SC007 to SC012 recompute or cross-check them. Round 2 showed that the
    round 1 checks were not enough: cards with impossible moments (skew 51.86, kurtosis 1),
    zero trial variance, or twice the possible number of observations still got grade A.
    SC011, SC012 and a tighter SC010 now reject them. Many inputs are still taken on trust
    (see `RULES.md`). A card is a disclosure, not a proof. *Tested* with the forged cards
    as fixtures.
17. **Tracebacks from extreme numbers.** `n_trials` 1e16 (StatisticsError), `n_observations`
    1e310 and Sharpe 1e308 (OverflowError) crashed `validate`; trial Sharpes of ±1e308
    crashed `fill`. The schema now has maxima, every rule's arithmetic errors become an
    error finding, and `fill` maps them to exit 2. *Tested*.
18. **Silent unit default.** `--periods-per-year` defaulted to 252. Monthly data then gave a
    Sharpe ratio of about 5 and hid SC104. The flag is now required. The round 1 check (median
    gap, factor 1.5) still let equity daily data pass as 365 (sample card D -> B) and got
    twice-weekly and every-other-day data wrong. The tool now infers (rows − 1) / calendar
    years, uses a factor of 1.15, and accepts more than 262 only with weekend dates. Dates
    must be exactly YYYY-MM-DD, so Python 3.10 and 3.12 parse the same inputs. *Tested*.

19. **Prompt injection through the Claude Code skill.** The skill tells Claude to treat
    card and CSV content as data, never as instructions. Its `allowed-tools` list names
    only file-read tools and `Bash(strategy-card fill:*)`, `Bash(strategy-card validate:*)`
    and `Bash(strategy-card badge:*)`. The skill text forbids other tools and forbids
    `--force` without the user's explicit OK. Before the review it allowed
    `Bash(strategy-card:*)` while `badge` could overwrite any file, so the "analysis only"
    claim for the skill was not true. In Claude Code, `allowed-tools` pre-approves tools; it does not by itself block
    other tools, so the host permission settings remain the real control. *Not testable by
    unit test.*

## Supply chain

- Runtime dependencies: `pyyaml` and `jsonschema` (which brings `referencing`,
  `jsonschema-specifications`, `attrs`, `rpds-py`).
- `pip-audit` over the full installed set (runtime and dev) on 2026-10-29:
  `No known vulnerabilities found`. Versions: pyyaml 6.0.3, jsonschema 4.26.0,
  referencing 0.37.0.
- The CI workflow pins `actions/checkout` and `actions/setup-python` by major tag, not by
  commit SHA. Pin them by SHA before you add secrets to the workflow. The workflow uses no
  secrets and has `contents: read` permission only.

## Out of scope

- A card can contain false claims. The validator checks consistency, not truth.
- Denial of service by a schema-valid card of 1 MiB is bounded by the size cap. The tool
  did not measure worst-case validation time.
