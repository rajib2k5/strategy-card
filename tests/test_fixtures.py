"""Seeded fixtures. File name convention: ``<RULE_ID>[+<RULE_ID>...]__<slug>.yaml``.

Every bad card must be rejected through the CLI with the right exit code AND its set of
ERROR rule IDs must equal the IDs in its file name (not just "fails"). Warnings on a bad
card are not part of the name. Warning cards pass normally and fail under --strict with
exactly their rule ID.
"""

from __future__ import annotations

import json

import pytest

from strategy_card.cli import main
from strategy_card.rules import INPUT_ID, RULES, SCHEMA_ID

from .conftest import FIXTURES

BAD = sorted((FIXTURES / "bad").glob("*.yaml"))
WARN = sorted((FIXTURES / "warn").glob("*.yaml"))
GOOD = sorted((FIXTURES / "good").glob("*.yaml"))

# For schema fixtures, also pin where the schema error is reported.
SCHEMA_PATHS = {
    "SC000__bad-survivorship-enum.yaml": "data.survivorship.handling",
    "SC000__dsr-above-one.yaml": "performance.deflated_sharpe.value",
    "SC000__empty-sources.yaml": "data.sources",
    "SC000__extra-key.yaml": "performance",
    "SC000__impossible-date.yaml": "test_window.in_sample.end",
    "SC000__missing-costs.yaml": "(root)",
    "SC000__negative-slippage.yaml": "costs.slippage_bps",
    "SC000__trials-count-wrong-type.yaml": "trials.count",
    "SC000__wrong-schema-version.yaml": "schema_version",
    "SC000__dsr-inputs-missing.yaml": "performance.deflated_sharpe",
    "SC000__octal-trial-count.yaml": "trials.count",
    "SC000__sexagesimal-fee.yaml": "costs.fees_bps",
    "SC000__fuzz-n-trials-1e16.yaml": "performance.deflated_sharpe.n_trials",
    "SC000__fuzz-n-observations-1e310.yaml": "performance.deflated_sharpe.n_observations",
    "SC000__fuzz-sharpe-1e308.yaml": "performance.deflated_sharpe.sharpe_annualized",
}


def expected_ids(path):
    return set(path.name.split("__", 1)[0].split("+"))


def expected_id(path):
    (only,) = expected_ids(path)
    return only


def run_json(capsys, *argv):
    code = main(["validate", "--format", "json", *argv])
    return code, json.loads(capsys.readouterr().out)


def test_fixture_sets_are_not_empty():
    assert len(GOOD) >= 2 and len(BAD) >= 15 and len(WARN) >= 6


def test_every_error_rule_has_a_bad_fixture():
    covered = set().union(*(expected_ids(p) for p in BAD))
    error_rules = {r.id for r in RULES if r.severity == "error"}
    assert error_rules | {SCHEMA_ID, INPUT_ID} <= covered


def test_every_warning_rule_has_a_warn_fixture():
    covered = {expected_id(p) for p in WARN}
    assert {r.id for r in RULES if r.severity == "warning"} <= covered


def test_sample_card_fixture_matches_example(tmp_path):
    from .conftest import ROOT

    assert (FIXTURES / "good" / "sample-card.yaml").read_text() == (
        ROOT / "examples" / "sample-card.yaml"
    ).read_text()


@pytest.mark.parametrize("path", BAD, ids=lambda p: p.name)
def test_bad_card_is_rejected_with_its_rule_id(path, capsys):
    rids = expected_ids(path)
    code, report = run_json(capsys, str(path))
    assert code == (2 if rids == {INPUT_ID} else 1)
    assert report["exit_code"] == code
    found = {f["id"] for f in report["findings"] if f["severity"] == "error"}
    assert found == rids, f"{path.name}: expected errors {sorted(rids)}, got {sorted(found)}"
    if rids == {SCHEMA_ID}:
        paths = {f["path"] for f in report["findings"]}
        assert SCHEMA_PATHS[path.name] in paths


@pytest.mark.parametrize("path", BAD, ids=lambda p: p.name)
def test_bad_card_also_fails_in_text_mode(path, capsys):
    code = main(["validate", str(path)])
    out = capsys.readouterr().out
    assert code in (1, 2)
    assert all(rid in out for rid in expected_ids(path))


@pytest.mark.parametrize("path", WARN, ids=lambda p: p.name)
def test_warning_card_passes_but_fails_strict(path, capsys):
    rid = expected_id(path)
    code, report = run_json(capsys, str(path))
    assert code == 0
    assert {f["id"] for f in report["findings"]} == {rid}
    assert all(f["severity"] == "warning" for f in report["findings"])
    strict_code, strict_report = run_json(capsys, "--strict", str(path))
    assert strict_code == 1 and strict_report["exit_code"] == 1


@pytest.mark.parametrize("path", GOOD, ids=lambda p: p.name)
def test_good_card_passes(path, capsys):
    code, report = run_json(capsys, str(path))
    assert code == 0
    assert all(f["severity"] == "warning" for f in report["findings"])
    assert report["grade"] in "ABCDE"


def test_single_trial_card_is_grade_a_and_passes_strict(capsys):
    code, report = run_json(capsys, "--strict", str(FIXTURES / "good" / "single-trial-no-dsr.yaml"))
    assert code == 0 and report["grade"] == "A" and report["findings"] == []
