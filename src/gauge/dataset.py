"""Dataset and suite loading. Datasets are YAML (list or ``cases:`` key) or JSONL, one case per line."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from .models import Case, Suite


class DatasetError(ValueError):
    pass


def load_cases(path: str | Path) -> list[Case]:
    p = Path(path)
    if not p.is_file():
        raise DatasetError(f"dataset not found: {p}")
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in (".jsonl", ".ndjson"):
        raw = []
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                raw.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise DatasetError(f"{p}:{number}: invalid JSON ({exc.msg})") from exc
    else:
        data = yaml.safe_load(text)
        raw = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise DatasetError(f"{p}: expected a list of cases")
    cases: list[Case] = []
    seen: set[str] = set()
    for index, item in enumerate(raw, 1):
        try:
            case = Case.model_validate(item)
        except Exception as exc:
            raise DatasetError(f"{p}: case #{index} is invalid: {exc}") from exc
        if case.id in seen:
            raise DatasetError(f"{p}: duplicate case id {case.id!r}")
        seen.add(case.id)
        cases.append(case)
    return cases


def load_suite(path: str | Path) -> Suite:
    p = Path(path)
    if not p.is_file():
        raise DatasetError(f"suite config not found: {p}")
    try:
        return Suite.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))
    except Exception as exc:
        raise DatasetError(f"{p}: invalid suite config: {exc}") from exc
