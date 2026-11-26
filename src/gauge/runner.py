"""Run a suite: execute the app on every case in parallel, trace its LLM calls, score the output."""
from __future__ import annotations

import contextvars
import importlib
import math
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .dataset import load_cases, load_suite
from .judge import Judge
from .models import Case, CaseResult, RunResult, RunSummary, ScoreResult, ScorerSpec, Suite
from .pricing import set_pricing
from .scorers import ScoreContext, get_scorer
from .tracing import capture


@dataclass
class AppOutput:
    """Return this from your app when scorers need more than the answer text (e.g. retrieved RAG passages)."""
    output: Any
    context: list[str] = field(default_factory=list)


def load_app(path: str) -> Callable[[Any], Any]:
    module_name, _, attr = path.partition(":")
    if not attr:
        raise ValueError(f"`app` must be 'module:function', got {path!r}")
    return getattr(importlib.import_module(module_name), attr)


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, math.ceil(pct / 100 * len(ordered)) - 1))
    return ordered[rank]


def git_info() -> dict[str, str | None]:
    def run(*args: str) -> str | None:
        try:
            out = subprocess.run(["git", *args], capture_output=True, text=True, timeout=10)
            return out.stdout.strip() or None if out.returncode == 0 else None
        except Exception:
            return None
    pr = None
    ref = os.environ.get("GITHUB_REF", "")
    if ref.startswith("refs/pull/"):
        pr = ref.split("/")[2]
    return {
        "commit": os.environ.get("GITHUB_SHA") or run("rev-parse", "HEAD"),
        "branch": os.environ.get("GITHUB_HEAD_REF") or os.environ.get("GITHUB_REF_NAME") or run("rev-parse", "--abbrev-ref", "HEAD"),
        "message": run("log", "-1", "--pretty=%s"),
        "pr": pr,
    }


def _score_case(case: Case, output: Any, suite_scorers: list[ScorerSpec], judge: Judge | None,
                retrieved: list[str] | None) -> list[ScoreResult]:
    results: list[ScoreResult] = []
    for spec in case.scorers or suite_scorers:
        ctx = ScoreContext(spec=spec, judge=judge, retrieved=retrieved)
        results.append(get_scorer(spec)(case, output, ctx))
    return results


def run_case(case: Case, app: Callable[[Any], Any], suite: Suite, judge: Judge | None) -> CaseResult:
    result = CaseResult(case_id=case.id, tags=case.tags, input=case.input, expected=case.expected)
    start = time.perf_counter()
    with capture() as calls:
        try:
            raw = app(case.input)
            result.latency_ms = (time.perf_counter() - start) * 1000
            retrieved = None
            if isinstance(raw, AppOutput):
                retrieved, raw = raw.context, raw.output
            result.output = raw
            app_calls = [c for c in calls if c.purpose == "app"]
            result.scores = _score_case(case, raw, suite.scorers, judge, retrieved)
        except Exception as exc:  # the app or a scorer failed: that is a failed case, never a crashed run
            if result.latency_ms == 0.0:
                result.latency_ms = (time.perf_counter() - start) * 1000
            result.error = f"{type(exc).__name__}: {exc}"[:500]
    result.calls = list(calls)
    app_calls = [c for c in result.calls if c.purpose == "app"]
    result.cost_usd = sum(c.cost_usd for c in app_calls)
    result.input_tokens = sum(c.input_tokens for c in app_calls)
    result.output_tokens = sum(c.output_tokens for c in app_calls)
    required = [s for s in result.scores if s.required]
    result.passed = result.error is None and bool(result.scores) and all(s.passed for s in required)
    result.score = (sum(s.score for s in result.scores) / len(result.scores)) if result.scores else 0.0
    return result


def summarize(results: list[CaseResult], duration_s: float) -> RunSummary:
    latencies = [r.latency_ms for r in results]
    passed = sum(1 for r in results if r.passed)
    judge_cost = sum(c.cost_usd for r in results for c in r.calls if c.purpose == "judge")
    return RunSummary(
        total=len(results), passed=passed, failed=len(results) - passed,
        errors=sum(1 for r in results if r.error), pass_rate=(passed / len(results)) if results else 0.0,
        cost_usd=sum(r.cost_usd for r in results), judge_cost_usd=judge_cost,
        input_tokens=sum(r.input_tokens for r in results), output_tokens=sum(r.output_tokens for r in results),
        latency_p50_ms=percentile(latencies, 50), latency_p95_ms=percentile(latencies, 95), duration_s=duration_s)


def run_suite(config_path: str | Path, *, only_tags: list[str] | None = None, limit: int | None = None,
              judge_mode: str | None = None) -> RunResult:
    config_path = Path(config_path).resolve()
    base = config_path.parent
    suite = load_suite(config_path)
    set_pricing(suite.pricing)
    cases = load_cases(base / suite.dataset)
    if only_tags:
        cases = [c for c in cases if set(only_tags) & set(c.tags)]
    if limit:
        cases = cases[:limit]
    # Make the app importable relative to the suite file, like pytest's rootdir.
    for entry in (str(base), str(base.parent)):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    app = load_app(suite.app)
    judge_cfg = dict(suite.judge)
    if judge_mode:
        judge_cfg["mode"] = judge_mode
    judge = Judge.from_config(judge_cfg, base) if judge_cfg else None

    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, suite.concurrency)) as pool:
        futures = [pool.submit(contextvars.copy_context().run, run_case, c, app, suite, judge) for c in cases]
        results = [f.result() for f in futures]
    duration = time.perf_counter() - t0
    info = git_info()
    return RunResult(
        suite=suite.name, commit=info["commit"], branch=info["branch"], pr=info["pr"], message=info["message"],
        started_at=started.isoformat(), summary=summarize(results, duration), thresholds=suite.thresholds,
        cases=results)
