import json
import sys
from pathlib import Path

import pytest

from gauge import observe, run_suite
from gauge.compare import compare, render_markdown
from gauge.models import Thresholds
from gauge.pricing import cost_usd
from gauge.tracing import capture, record_call


def write_suite(tmp_path: Path, app: str, scorers: str = "[{type: exact_match}]") -> Path:
    (tmp_path / "data.jsonl").write_text("\n".join(json.dumps(c) for c in [
        {"id": "a", "input": "2+2", "expected": "4", "tags": ["math"]},
        {"id": "b", "input": "3+3", "expected": "6", "tags": ["math"]},
        {"id": "c", "input": "capital of France", "expected": "paris"},
    ]))
    cfg = tmp_path / "gauge.yml"
    cfg.write_text(f"name: t\ndataset: data.jsonl\napp: {app}\nscorers: {scorers}\n")
    return cfg


def test_pricing_known_and_unknown_models():
    assert cost_usd("claude-haiku-5-5", 1_000_000, 1_000_000) == pytest.approx(0.60)
    assert cost_usd("claude-haiku-5-5-20261101", 1_000_000, 0) == pytest.approx(0.10)
    assert cost_usd("mystery-model", 1000, 1000) == 0.0


def test_capture_and_observe():
    @observe(model="claude-haiku-5-5")
    def f(x):
        return "out", {"input_tokens": 1000, "output_tokens": 500}

    with capture() as calls:
        f("hi")
        record_call(model="m", input_tokens=1, output_tokens=1, purpose="judge")
    assert len(calls) == 2 and calls[0].cost_usd == pytest.approx(0.00035) and calls[1].purpose == "judge"


def test_run_suite_end_to_end(tmp_path):
    (tmp_path / "myapp.py").write_text(
        "from gauge import observe\n"
        "@observe(model='claude-haiku-5-5')\n"
        "def llm(q):\n"
        "    ans = {'2+2': '4', '3+3': '7', 'capital of France': 'Paris'}[q]\n"
        "    return ans, {'input_tokens': 100, 'output_tokens': 10}\n"
        "def app(q):\n"
        "    return llm(q)[0]\n")
    run = run_suite(write_suite(tmp_path, "myapp:app"))
    assert run.summary.total == 3 and run.summary.passed == 2
    assert run.summary.pass_rate == pytest.approx(2 / 3)
    bad = next(c for c in run.cases if c.case_id == "b")
    assert not bad.passed and "expected" in bad.scores[0].reason
    assert run.summary.cost_usd > 0 and run.cases[0].input_tokens == 100
    assert run_suite(write_suite(tmp_path, "myapp:app"), only_tags=["math"]).summary.total == 2


def test_app_errors_are_failed_cases_not_crashes(tmp_path):
    (tmp_path / "boom.py").write_text("def app(q):\n    raise RuntimeError('kaput')\n")
    run = run_suite(write_suite(tmp_path, "boom:app"))
    assert run.summary.passed == 0 and run.summary.errors == 3 and "kaput" in run.cases[0].error


def test_rag_context_from_app_output(tmp_path):
    (tmp_path / "ragapp.py").write_text(
        "from gauge import AppOutput\n"
        "def app(q):\n    return AppOutput(output='answer', context=['retrieved passage'])\n")
    (tmp_path / "checks.py").write_text(
        "from gauge.models import ScoreResult\n"
        "def ctx(case, output, ctx):\n"
        "    return ScoreResult(name='ctx', passed=ctx.retrieved == ['retrieved passage'] and output == 'answer')\n")
    cfg = write_suite(tmp_path, "ragapp:app", scorers="[{type: python, path: 'checks:ctx'}]")
    assert run_suite(cfg).summary.passed == 3


_counter = iter(range(10_000))


def _run(tmp_path, answers):
    name = f"mod_{next(_counter)}"  # unique name: avoids stale .pyc when rewritten within the same second
    (tmp_path / f"{name}.py").write_text(f"ANS = {answers!r}\ndef app(q):\n    return ANS[q]\n")
    return run_suite(write_suite(tmp_path, f"{name}:app"))


GOOD = {"2+2": "4", "3+3": "6", "capital of France": "paris"}


def test_compare_detects_regression_and_renders(tmp_path):
    base = _run(tmp_path, GOOD)
    assert base.summary.pass_rate == 1.0
    assert compare(base, base).passed

    worse = _run(tmp_path, {**GOOD, "3+3": "7"})
    verdict = compare(worse, base)
    assert not verdict.passed
    assert [c.case_id for c in verdict.new_failures] == ["b"]
    md = render_markdown(worse, verdict)
    assert "REGRESSION DETECTED" in md and "<code>b</code>" in md and "expected" in md

    lenient = Thresholds(max_pass_rate_drop=0.5, max_new_failures=1)
    assert compare(worse, base, lenient).passed

    better = compare(base, worse)
    assert better.passed and better.fixed == ["b"]


def test_compare_without_baseline_checks_floor_only(tmp_path):
    run = _run(tmp_path, GOOD)
    assert compare(run, None).passed
    assert not compare(run, None, Thresholds(min_pass_rate=1.1)).passed


def test_cost_and_latency_gates(tmp_path):
    base = _run(tmp_path, GOOD)
    base.summary.cost_usd = 1.0
    base.summary.latency_p95_ms = 100.0
    cur = base.model_copy(deep=True)
    cur.summary.cost_usd = 1.5
    assert any("cost rose" in r for r in compare(cur, base, Thresholds(max_cost_increase=0.25)).reasons)
    cur.summary.cost_usd = 1.0
    cur.summary.latency_p95_ms = 300.0
    assert any("latency" in r for r in compare(cur, base, Thresholds(max_latency_increase=0.5)).reasons)
