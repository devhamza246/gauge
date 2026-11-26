"""Deterministic scorers: free, instant and exact. Prefer these before reaching for an LLM judge."""
from __future__ import annotations

import json
import re
from typing import Any

import jsonschema

from ..models import Case, ScoreResult
from . import ScoreContext, register


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)


def _result(ctx: ScoreContext, passed: bool, reason: str, score: float | None = None) -> ScoreResult:
    return ScoreResult(name=ctx.spec.label, passed=passed, score=(1.0 if passed else 0.0) if score is None else score,
                       reason=reason, required=ctx.spec.required)


@register("exact_match")
def exact_match(case: Case, output: Any, ctx: ScoreContext) -> ScoreResult:
    if case.expected is None:
        return _result(ctx, False, "case has no `expected` value")
    if isinstance(case.expected, (dict, list)) or isinstance(output, (dict, list)):
        ok = output == case.expected
        return _result(ctx, ok, "equal" if ok else f"expected {_text(case.expected)[:160]}, got {_text(output)[:160]}")
    a, b = str(output), str(case.expected)
    if ctx.params.get("normalize", True):
        a, b = " ".join(a.lower().split()).strip(" .!"), " ".join(b.lower().split()).strip(" .!")
    return _result(ctx, a == b, "equal" if a == b else f"expected {b[:160]!r}, got {a[:160]!r}")


@register("contains")
def contains(case: Case, output: Any, ctx: ScoreContext) -> ScoreResult:
    needles = ctx.params.get("values") or ([case.expected] if case.expected is not None else [])
    if not needles:
        return _result(ctx, False, "nothing to look for: set `values` or the case `expected`")
    haystack = _text(output)
    if not ctx.params.get("case_sensitive", False):
        haystack = haystack.lower()
        needles = [str(n).lower() for n in needles]
    missing = [n for n in needles if str(n) not in haystack]
    if ctx.params.get("any", False):
        ok = len(missing) < len(needles)
    else:
        ok = not missing
    return _result(ctx, ok, "found" if ok else f"missing {missing}")


@register("not_contains")
def not_contains(case: Case, output: Any, ctx: ScoreContext) -> ScoreResult:
    banned = ctx.params.get("values") or []
    haystack = _text(output).lower()
    hits = [b for b in banned if str(b).lower() in haystack]
    return _result(ctx, not hits, "clean" if not hits else f"contains forbidden {hits}")


@register("regex")
def regex(case: Case, output: Any, ctx: ScoreContext) -> ScoreResult:
    pattern = ctx.params.get("pattern") or (str(case.expected) if case.expected is not None else None)
    if not pattern:
        return _result(ctx, False, "no `pattern` configured")
    ok = re.search(pattern, _text(output), flags=re.IGNORECASE | re.DOTALL) is not None
    return _result(ctx, ok, "matched" if ok else f"no match for /{pattern}/")


@register("json_schema")
def json_schema(case: Case, output: Any, ctx: ScoreContext) -> ScoreResult:
    schema = ctx.params.get("schema")
    if not schema:
        return _result(ctx, False, "no `schema` configured")
    data = output
    if isinstance(output, str):
        try:
            data = json.loads(output)
        except json.JSONDecodeError as exc:
            return _result(ctx, False, f"output is not valid JSON: {exc.msg}")
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        path = "/".join(str(p) for p in exc.absolute_path) or "(root)"
        return _result(ctx, False, f"schema violation at {path}: {exc.message[:160]}")
    return _result(ctx, True, "valid")


@register("field_match")
def field_match(case: Case, output: Any, ctx: ScoreContext) -> ScoreResult:
    """Compare selected top-level fields of a dict output with the case's `expected` dict."""
    if not isinstance(output, dict) or not isinstance(case.expected, dict):
        return _result(ctx, False, "field_match needs a dict output and a dict `expected`")
    fields = ctx.params.get("fields") or list(case.expected)
    wrong = [f for f in fields if output.get(f) != case.expected.get(f)]
    score = (len(fields) - len(wrong)) / len(fields) if fields else 0.0
    ok = score >= float(ctx.params.get("threshold", 1.0))
    return _result(ctx, ok, "all fields match" if not wrong else f"mismatched fields: {wrong}", score)


@register("max_length")
def max_length(case: Case, output: Any, ctx: ScoreContext) -> ScoreResult:
    limit = int(ctx.params.get("chars", 1000))
    n = len(_text(output))
    return _result(ctx, n <= limit, f"{n} characters (limit {limit})")
