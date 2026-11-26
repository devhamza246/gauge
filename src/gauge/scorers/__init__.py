"""Scorer registry. A scorer is ``fn(case, output, ctx) -> ScoreResult`` where ``ctx`` is a ScoreContext."""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Callable

from ..judge import Judge
from ..models import Case, ScoreResult, ScorerSpec


@dataclass
class ScoreContext:
    spec: ScorerSpec
    judge: Judge | None = None
    retrieved: list[str] | None = None   # context the app actually used (RAG), if it reported any
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def params(self) -> dict[str, Any]:
        return self.spec.params


Scorer = Callable[[Case, Any, ScoreContext], ScoreResult]
REGISTRY: dict[str, Scorer] = {}


def register(name: str) -> Callable[[Scorer], Scorer]:
    def deco(fn: Scorer) -> Scorer:
        REGISTRY[name] = fn
        return fn
    return deco


def load_python_scorer(path: str) -> Scorer:
    module_name, _, attr = path.partition(":")
    if not attr:
        raise ValueError(f"python scorer must be 'module:function', got {path!r}")
    return getattr(importlib.import_module(module_name), attr)


def get_scorer(spec: ScorerSpec) -> Scorer:
    if spec.type == "python":
        return load_python_scorer(spec.params["path"])
    if spec.type not in REGISTRY:
        raise ValueError(f"unknown scorer type {spec.type!r}; available: {sorted(REGISTRY)} + 'python'")
    return REGISTRY[spec.type]


from . import deterministic, llm  # noqa: E402,F401  (registers the built-in scorers)
