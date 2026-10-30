"""Validation pipeline: input -> placeholders + schema -> logic rules."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator

from .grading import Grade, grade
from .loader import InputError, load_card
from .rules import ERROR, INPUT_ID, SCHEMA_ID, WARNING, Finding, find_placeholders, run_rules

SCHEMA_RESOURCE = "strategy-card-0.1.schema.json"

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_INPUT = 2


def schema_text() -> str:
    return resources.files("strategy_card").joinpath("schema", SCHEMA_RESOURCE).read_text("utf-8")


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = json.loads(schema_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def _path(parts) -> str:
    return ".".join(str(p) for p in parts) or "(root)"


def schema_findings(card: Any) -> list[Finding]:
    errors = sorted(
        _validator().iter_errors(card), key=lambda e: (list(map(str, e.path)), e.message)
    )
    return [
        Finding(SCHEMA_ID, "schema", ERROR, _path(e.absolute_path), e.message[:300]) for e in errors
    ]


@dataclass
class Report:
    file: str
    findings: list[Finding] = field(default_factory=list)
    input_error: bool = False

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == WARNING]

    @property
    def grade(self) -> Grade | None:
        return None if self.input_error else grade(self.findings)

    def exit_code(self, strict: bool = False) -> int:
        if self.input_error:
            return EXIT_INPUT
        if self.errors or (strict and self.warnings):
            return EXIT_FINDINGS
        return EXIT_OK

    def as_dict(self, strict: bool = False) -> dict[str, Any]:
        g = self.grade
        return {
            "file": self.file,
            "exit_code": self.exit_code(strict),
            "grade": g.letter if g else None,
            "findings": [f.as_dict() for f in self.findings],
        }


def validate_card(card: Any, file: str = "<card>") -> Report:
    placeholders = find_placeholders(card)
    placeholder_paths = {f.path for f in placeholders}
    schema = [f for f in schema_findings(card) if f.path not in placeholder_paths]
    findings = placeholders + schema
    if findings:
        return Report(file, findings)
    return Report(file, run_rules(card))


def validate_file(path: str | os.PathLike) -> Report:
    try:
        card = load_card(path)
    except InputError as exc:
        return Report(
            str(path),
            [Finding(INPUT_ID, "input-invalid", ERROR, "(file)", str(exc)[:500])],
            input_error=True,
        )
    return validate_card(card, str(path))
