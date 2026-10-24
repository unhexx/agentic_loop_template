# -*- coding: utf-8 -*-
"""Draft 2020-12 checks. Tool objects reject unknown properties."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

_SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"


def load_schema(stem: str) -> dict:
    path = _SCHEMA_DIR / f"{stem}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _validator(stem: str) -> Draft202012Validator:
    schema = load_schema(stem)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def argument_errors(name: str, arguments: dict) -> list[str]:
    """Human-readable schema violations. Empty means the arguments are valid."""
    errors = sorted(
        _validator(name).iter_errors(arguments),
        key=lambda err: (list(err.absolute_path), err.message),
    )
    rendered: list[str] = []
    for err in errors:
        path = ".".join(str(part) for part in err.absolute_path) or "(root)"
        rendered.append(f"{path}: {err.message}")
    return rendered


def result_errors(payload: dict) -> list[str]:
    errors = sorted(
        _validator("local_pipeline_result").iter_errors(payload),
        key=lambda err: (list(err.absolute_path), err.message),
    )
    return [err.message for err in errors]


def format_argument_errors(errors: list[str]) -> str:
    text = "; ".join(errors) if errors else "Invalid params"
    if len(text) > 500:
        return text[:497] + "..."
    return text
