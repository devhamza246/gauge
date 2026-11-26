"""Command line interface: gauge run | compare | ci | baseline | calibrate | import | serve."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

from .compare import compare, render_markdown
from .dataset import DatasetError
from .models import RunResult, RunSummary
from .runner import percentile, run_suite


def _write_json(path: str, run: RunResult) -> None:
    Path(path).write_text(run.model_dump_json(indent=2), encoding="utf-8")


def _read_run(path: str) -> RunResult:
    return RunResult.model_validate_json(Path(path).read_text(encoding="utf-8"))


def _server_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--server", default=os.environ.get("GAUGE_SERVER"), help="Gauge server URL (or GAUGE_SERVER)")
    p.add_argument("--token", default=os.environ.get("GAUGE_TOKEN"), help="ingest token (or GAUGE_TOKEN)")


def _push(run: RunResult, server: str | None, token: str | None) -> str | None:
    if not server:
        return None
    response = httpx.post(f"{server.rstrip('/')}/api/runs", content=run.model_dump_json(),
                          headers={"authorization": f"Bearer {token or ''}", "content-type": "application/json"},
                          timeout=60)
    if response.status_code >= 400:
        print(f"warning: could not upload the run to {server}: {response.status_code} {response.text[:160]}",
              file=sys.stderr)
        return None
    return response.json().get("id")


def _fetch_baseline(server: str, token: str | None, suite: str, branch: str) -> RunResult | None:
    response = httpx.get(f"{server.rstrip('/')}/api/baseline", params={"suite": suite, "branch": branch},
                         headers={"authorization": f"Bearer {token or ''}"}, timeout=30)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return RunResult.model_validate(response.json())


def _print_summary(run: RunResult) -> None:
    s = run.summary
    print(f"{run.suite}: {s.passed}/{s.total} passed ({s.pass_rate:.1%}) | cost ${s.cost_usd:.4f} "
          f"(+judge ${s.judge_cost_usd:.4f}) | p50 {s.latency_p50_ms:.0f} ms p95 {s.latency_p95_ms:.0f} ms | "
          f"{s.duration_s:.1f}s", flush=True)


def cmd_run(args: argparse.Namespace) -> int:
    run = run_suite(args.config, only_tags=args.tag, limit=args.limit, judge_mode=args.judge_mode)
    _print_summary(run)
    for case in run.cases:
        if not case.passed:
            reason = case.error or "; ".join(s.reason for s in case.scores if not s.passed)
            print(f"  FAIL {case.case_id}: {reason[:200]}")
    if args.out:
        _write_json(args.out, run)
    if args.db:
        from .store import Store
        print(f"saved run {Store(args.db).save(run)}")
    run_id = _push(run, args.server, args.token)
    if run_id:
        print(f"uploaded run {run_id}")
    return 0 if args.allow_failures or run.summary.failed == 0 else 1


def cmd_baseline(args: argparse.Namespace) -> int:
    run = run_suite(args.config, judge_mode=args.judge_mode)
    _print_summary(run)
    _write_json(args.out, run)
    print(f"baseline written to {args.out}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    current = _read_run(args.results)
    baseline = _read_run(args.baseline) if args.baseline and Path(args.baseline).is_file() else None
    verdict = compare(current, baseline)
    text = render_markdown(current, verdict)
    print(text)
    if args.md:
        Path(args.md).write_text(text, encoding="utf-8")
    return 0 if verdict.passed else 1


def cmd_ci(args: argparse.Namespace) -> int:
    run = run_suite(args.config, judge_mode=args.judge_mode or "replay")
    _print_summary(run)
    baseline: RunResult | None = None
    if args.baseline and Path(args.baseline).is_file():
        baseline = _read_run(args.baseline)
    elif args.server:
        baseline = _fetch_baseline(args.server, args.token, run.suite, args.base_branch)
    verdict = compare(run, baseline)
    text = render_markdown(run, verdict)
    print(text)
    if args.md:
        Path(args.md).write_text(text, encoding="utf-8")
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as handle:
            handle.write(text)
    if args.out:
        _write_json(args.out, run)
    _push(run, args.server, args.token)
    return 0 if verdict.passed else 1


def cmd_calibrate(args: argparse.Namespace) -> int:
    """Measure how often the judge agrees with human labels. Labels: JSONL of
    {"id","input","output","expected"?,"context"?,"human_pass": bool} and a scorer from gauge.yml."""
    from .dataset import load_suite
    from .judge import Judge
    from .models import Case
    from .scorers import ScoreContext, get_scorer
    config = Path(args.config).resolve()
    suite = load_suite(config)
    judge = Judge.from_config(dict(suite.judge, **({"mode": args.judge_mode} if args.judge_mode else {})), config.parent)
    spec = next((s for s in suite.scorers if s.type in ("llm_judge", "rag_faithfulness")), None)
    if spec is None:
        print("no llm_judge or rag_faithfulness scorer in the suite", file=sys.stderr)
        return 2
    agree = total = 0
    for line in Path(args.labels).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        case = Case(id=item["id"], input=item.get("input"), expected=item.get("expected"),
                    rubric=item.get("rubric"), context=item.get("context"))
        result = get_scorer(spec)(case, item["output"], ScoreContext(spec=spec, judge=judge))
        same = result.passed == bool(item["human_pass"])
        agree += same
        total += 1
        print(f"{'ok ' if same else 'DIFF'} {item['id']}: judge={result.passed} human={item['human_pass']} {result.reason[:90]}")
    rate = agree / total if total else 0.0
    print(f"agreement: {agree}/{total} = {rate:.0%} (target >= {args.target:.0%})")
    return 0 if rate >= args.target else 1


def cmd_import(args: argparse.Namespace) -> int:
    """Import results from another harness. Input: {"suite","commit"?,"branch"?,"cases":[{"id","passed",...}]}."""
    from .models import CaseResult, ScoreResult
    data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    cases = []
    for item in data["cases"]:
        cases.append(CaseResult(
            case_id=str(item["id"]), tags=item.get("tags", []), input=item.get("input"), expected=item.get("expected"),
            output=item.get("output"), passed=bool(item["passed"]), score=float(item.get("score", 1.0 if item["passed"] else 0.0)),
            scores=[ScoreResult(name=item.get("scorer", "imported"), passed=bool(item["passed"]), reason=item.get("reason", ""))],
            latency_ms=float(item.get("latency_ms", 0)), cost_usd=float(item.get("cost_usd", 0)),
            input_tokens=int(item.get("input_tokens", 0)), output_tokens=int(item.get("output_tokens", 0)),
            error=item.get("error")))
    lat = [c.latency_ms for c in cases]
    passed = sum(c.passed for c in cases)
    run = RunResult(
        suite=args.suite or data["suite"], commit=args.commit or data.get("commit"), branch=args.branch or data.get("branch"),
        message=data.get("message"), started_at=data.get("started_at"), source=data.get("source", "import"),
        summary=RunSummary(total=len(cases), passed=passed, failed=len(cases) - passed,
                           pass_rate=passed / len(cases) if cases else 0.0, cost_usd=sum(c.cost_usd for c in cases),
                           input_tokens=sum(c.input_tokens for c in cases), output_tokens=sum(c.output_tokens for c in cases),
                           latency_p50_ms=percentile(lat, 50), latency_p95_ms=percentile(lat, 95)),
        cases=cases)
    if args.out:
        _write_json(args.out, run)
    run_id = _push(run, args.server, args.token)
    if args.db:
        from .store import Store
        run_id = Store(args.db).save(run)
    print(f"imported {len(cases)} cases ({passed} passed)" + (f"; run {run_id}" if run_id else ""))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn
    uvicorn.run("gauge.server.api:app", host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gauge", description="Regression-test, trace and cost-track LLM apps.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="run the suite and print results")
    p.add_argument("-c", "--config", default="gauge.yml")
    p.add_argument("--out", help="write the full results JSON here")
    p.add_argument("--db", help="also save to this database URL (sqlite:/// or postgresql://)")
    p.add_argument("--tag", action="append", help="only cases with this tag (repeatable)")
    p.add_argument("--limit", type=int)
    p.add_argument("--judge-mode", choices=["auto", "live", "replay"])
    p.add_argument("--allow-failures", action="store_true", help="exit 0 even if cases fail")
    _server_args(p)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("baseline", help="run the suite and write a baseline file")
    p.add_argument("-c", "--config", default="gauge.yml")
    p.add_argument("--out", default="gauge-baseline.json")
    p.add_argument("--judge-mode", choices=["auto", "live", "replay"])
    p.set_defaults(fn=cmd_baseline)

    p = sub.add_parser("compare", help="compare a results file with a baseline file")
    p.add_argument("--results", required=True)
    p.add_argument("--baseline")
    p.add_argument("--md")
    p.set_defaults(fn=cmd_compare)

    p = sub.add_parser("ci", help="run, compare with the baseline, write a PR summary, exit 1 on regression")
    p.add_argument("-c", "--config", default="gauge.yml")
    p.add_argument("--baseline", default="gauge-baseline.json", help="baseline file (preferred if it exists)")
    p.add_argument("--base-branch", default="main", help="branch whose latest server run is the baseline")
    p.add_argument("--md")
    p.add_argument("--out")
    p.add_argument("--judge-mode", choices=["auto", "live", "replay"])
    _server_args(p)
    p.set_defaults(fn=cmd_ci)

    p = sub.add_parser("calibrate", help="measure judge agreement with human labels")
    p.add_argument("-c", "--config", default="gauge.yml")
    p.add_argument("--labels", required=True)
    p.add_argument("--target", type=float, default=0.9)
    p.add_argument("--judge-mode", choices=["auto", "live", "replay"])
    p.set_defaults(fn=cmd_calibrate)

    p = sub.add_parser("import", help="import results from another harness")
    p.add_argument("file")
    p.add_argument("--suite")
    p.add_argument("--commit")
    p.add_argument("--branch")
    p.add_argument("--out")
    p.add_argument("--db")
    _server_args(p)
    p.set_defaults(fn=cmd_import)

    p = sub.add_parser("serve", help="run the API server (needs gauge-eval[server])")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(fn=cmd_serve)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except (DatasetError, ValueError, KeyError) as exc:
        print(f"gauge: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
