from __future__ import annotations

import json
import subprocess
import sys

import pytest

from strategy_card.cli import main

from .conftest import FIXTURES, ROOT


def test_schema_command_prints_valid_draft_2020_12_schema(capsys):
    from jsonschema import Draft202012Validator

    assert main(["schema"]) == 0
    schema = json.loads(capsys.readouterr().out)
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_text_output_lists_findings(capsys):
    code = main(["validate", str(ROOT / "examples" / "sample-card.yaml")])
    out = capsys.readouterr().out
    assert code == 0
    assert "grade D" in out and "SC104" in out and "SC105" in out


def test_strict_turns_warnings_into_exit_1(capsys):
    assert main(["validate", "--strict", str(ROOT / "examples" / "sample-card.yaml")]) == 1


def test_json_output_shape(capsys):
    main(
        [
            "validate",
            "--format",
            "json",
            str(FIXTURES / "bad" / "SC004__dsr-n-trials-mismatch.yaml"),
        ]
    )
    d = json.loads(capsys.readouterr().out)
    assert set(d) == {"file", "exit_code", "grade", "findings"}
    assert set(d["findings"][0]) == {"id", "name", "severity", "path", "message", "flag"}


def test_control_characters_in_file_name_are_escaped_in_text_output(tmp_path, capsys):
    """Raw text reaches the terminal through the file name (kills mutant M36).

    YAML keys and values do not: PyYAML refuses raw control characters, and messages
    show escaped keys through repr().
    """
    p = tmp_path / "card\x1b]0;pwned\x07\x1b[31m.yaml"
    p.write_text((ROOT / "examples" / "sample-card.yaml").read_text())
    main(["validate", str(p)])
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\x07" not in out
    assert "\\x1b" in out  # shown escaped, not dropped


def test_missing_file_exits_2(capsys):
    assert main(["validate", "/nonexistent/card.yaml"]) == 2


def test_console_script_runs_as_module():
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "strategy_card.cli",
            "validate",
            str(ROOT / "examples" / "sample-card.yaml"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("module", ["socket", "urllib.request", "http.client"])
def test_package_never_imports_network_modules(module):
    """Analysis only: no network. Checked in a fresh interpreter, dependencies included."""
    code = (
        "import sys; import strategy_card.cli, strategy_card.fill, strategy_card.badge; "
        f"print({module!r} in sys.modules)"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert r.stdout.strip() == "False"


def test_package_source_has_no_shell_or_network_calls():
    """Policy check on our own code. (On Python 3.10 the stdlib ``uuid`` module, imported
    by jsonschema, pulls in ``subprocess`` itself, so a sys.modules check cannot be used.)"""
    import ast

    banned_modules = {"subprocess", "socket", "urllib", "http", "requests", "pty", "shlex"}
    banned_calls = {("os", "system"), ("os", "popen"), ("os", "execv"), ("os", "spawnv")}
    for path in (ROOT / "src" / "strategy_card").rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                names = []
            for name in names:
                assert name.split(".")[0] not in banned_modules, f"{path.name} imports {name}"
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                assert (node.value.id, node.attr) not in banned_calls, (
                    f"{path.name} uses os.{node.attr}"
                )
            if isinstance(node, ast.Name):
                assert node.id not in {"eval", "exec"}, f"{path.name} uses {node.id}"


def test_readme_inline_sample_card_matches_example():
    import re

    import yaml

    readme = (ROOT / "README.md").read_text()
    block = re.search(r"```yaml\n(.*?)```", readme, re.S).group(1)
    sample = (ROOT / "examples" / "sample-card.yaml").read_text()
    assert yaml.safe_load(block) == yaml.safe_load(sample)


def test_non_utf8_stdout_does_not_crash(tmp_path):
    """Review item 6: PYTHONIOENCODING=ascii with Cyrillic card text."""
    import os

    p = tmp_path / "card.yaml"
    # The unknown key is echoed in the SC000 message, so Cyrillic reaches stdout.
    p.write_text('schema_version: "0.1"\nПривет: 1\n', encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="ascii")
    r = subprocess.run(
        [sys.executable, "-m", "strategy_card.cli", "validate", str(p)],
        capture_output=True,
        text=True,
        env=env,
        encoding="ascii",
        errors="replace",
        timeout=30,
    )
    assert "Traceback" not in r.stderr
    assert r.returncode == 1
    assert "SC000" in r.stdout


def test_skill_allowed_tools_are_narrow_and_force_needs_consent():
    """Review item 3: the skill may run only the subcommands it needs."""
    import yaml

    text = (ROOT / "skills" / "strategy-card-fill" / "SKILL.md").read_text()
    front = yaml.safe_load(text.split("---")[1])
    tools = {t.strip() for t in front["allowed-tools"].split(",")}
    assert "Bash(strategy-card:*)" not in tools
    assert {
        "Bash(strategy-card fill:*)",
        "Bash(strategy-card validate:*)",
        "Bash(strategy-card badge:*)",
    } <= tools
    assert not any(t.startswith(("Write", "Edit", "WebFetch", "WebSearch")) for t in tools)
    assert "never pass `--force`" in text.lower() or "never passes `--force`" in text.lower()
