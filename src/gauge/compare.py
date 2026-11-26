"""Compare a run with a baseline and render the verdict (pass or regression) as markdown."""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import CaseResult, RunResult, Thresholds


@dataclass
class Verdict:
    passed: bool
    reasons: list[str] = field(default_factory=list)       # why it failed
    new_failures: list[CaseResult] = field(default_factory=list)
    fixed: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)       # baseline cases absent from this run
    baseline: RunResult | None = None


def compare(current: RunResult, baseline: RunResult | None, thresholds: Thresholds | None = None) -> Verdict:
    t = thresholds or current.thresholds
    verdict = Verdict(passed=True, baseline=baseline)
    cs = current.summary
    if t.min_pass_rate is not None and cs.pass_rate < t.min_pass_rate:
        verdict.reasons.append(f"pass rate {cs.pass_rate:.1%} is below the required minimum {t.min_pass_rate:.1%}")
    if baseline is None:
        verdict.passed = not verdict.reasons
        return verdict

    bs = baseline.summary
    drop = bs.pass_rate - cs.pass_rate
    if drop > t.max_pass_rate_drop + 1e-9:
        verdict.reasons.append(
            f"pass rate fell {drop * 100:.1f} points ({bs.pass_rate:.1%} to {cs.pass_rate:.1%}); "
            f"limit is {t.max_pass_rate_drop * 100:.1f}")
    base_by_id = {c.case_id: c for c in baseline.cases}
    cur_by_id = {c.case_id: c for c in current.cases}
    for case_id, case in cur_by_id.items():
        before = base_by_id.get(case_id)
        if before and before.passed and not case.passed:
            verdict.new_failures.append(case)
        elif before and not before.passed and case.passed:
            verdict.fixed.append(case_id)
    verdict.missing = [i for i in base_by_id if i not in cur_by_id]
    if len(verdict.new_failures) > t.max_new_failures:
        verdict.reasons.append(
            f"{len(verdict.new_failures)} case(s) that passed on the baseline now fail; limit is {t.max_new_failures}")
    if bs.cost_usd > 0 and (cs.cost_usd - bs.cost_usd) / bs.cost_usd > t.max_cost_increase + 1e-9:
        verdict.reasons.append(
            f"cost rose {(cs.cost_usd - bs.cost_usd) / bs.cost_usd:.0%} "
            f"(${bs.cost_usd:.4f} to ${cs.cost_usd:.4f}); limit is {t.max_cost_increase:.0%}")
    if bs.latency_p95_ms > 0 and (cs.latency_p95_ms - bs.latency_p95_ms) / bs.latency_p95_ms > t.max_latency_increase + 1e-9:
        verdict.reasons.append(
            f"p95 latency rose {(cs.latency_p95_ms - bs.latency_p95_ms) / bs.latency_p95_ms:.0%} "
            f"({bs.latency_p95_ms:.0f} ms to {cs.latency_p95_ms:.0f} ms); limit is {t.max_latency_increase:.0%}")
    verdict.passed = not verdict.reasons
    return verdict


def _clip(value: object, n: int = 400) -> str:
    text = value if isinstance(value, str) else repr(value)
    text = text.replace("\r", "")
    return text if len(text) <= n else text[:n] + "..."


def render_markdown(current: RunResult, verdict: Verdict, max_cases: int = 10) -> str:
    cs = current.summary
    status = "PASSED" if verdict.passed else "REGRESSION DETECTED"
    icon = "✅" if verdict.passed else "❌"
    lines = [f"## {icon} Gauge: {current.suite}: {status}", ""]
    lines.append("| | This run | Baseline |")
    lines.append("|---|---|---|")
    b = verdict.baseline.summary if verdict.baseline else None
    def cell(fmt: str, attr: str) -> str:
        return fmt.format(getattr(b, attr)) if b else "n/a"
    lines.append(f"| Pass rate | {cs.pass_rate:.1%} ({cs.passed}/{cs.total}) | "
                 f"{(f'{b.pass_rate:.1%} ({b.passed}/{b.total})') if b else 'n/a'} |")
    lines.append(f"| Cost | ${cs.cost_usd:.4f} | {cell('${:.4f}', 'cost_usd')} |")
    lines.append(f"| Latency p50 / p95 | {cs.latency_p50_ms:.0f} / {cs.latency_p95_ms:.0f} ms | "
                 f"{(f'{b.latency_p50_ms:.0f} / {b.latency_p95_ms:.0f} ms') if b else 'n/a'} |")
    lines.append(f"| Tokens in / out | {cs.input_tokens} / {cs.output_tokens} | "
                 f"{(f'{b.input_tokens} / {b.output_tokens}') if b else 'n/a'} |")
    if current.commit:
        lines.append(f"\nCommit `{current.commit[:10]}`" + (f" on `{current.branch}`" if current.branch else ""))
    if verdict.baseline is None:
        lines.append("\n_No baseline yet: this run only checks absolute limits. Commit a baseline to enable the regression gate._")
    if verdict.reasons:
        lines.append("\n**Why it failed**")
        lines.extend(f"- {r}" for r in verdict.reasons)
    failing = verdict.new_failures or ([c for c in current.cases if not c.passed] if verdict.baseline is None else [])
    if failing:
        title = "Cases that passed on the baseline and now fail" if verdict.new_failures else "Failing cases"
        lines.append(f"\n**{title}** ({len(failing)})")
        for case in failing[:max_cases]:
            lines.append(f"\n<details><summary><code>{case.case_id}</code>"
                         + (f" · {', '.join(case.tags)}" if case.tags else "") + "</summary>\n")
            lines.append(f"- **input:** {_clip(case.input, 300)}")
            if case.expected is not None:
                lines.append(f"- **expected:** {_clip(case.expected, 300)}")
            lines.append(f"- **got:** {_clip(case.output) if case.error is None else '(error) ' + (case.error or '')}")
            for s in case.scores:
                lines.append(f"- {'✅' if s.passed else '❌'} `{s.name}`: {_clip(s.reason, 240)}")
            app_calls = [c for c in case.calls if c.purpose == "app"]
            if app_calls:
                call = app_calls[0]
                lines.append(f"- **trace:** {call.model}, {call.input_tokens} in / {call.output_tokens} out tokens, "
                             f"{call.latency_ms:.0f} ms, ${call.cost_usd:.5f}")
                if call.prompt:
                    lines.append(f"\n```text\n{_clip(call.prompt, 600)}\n```")
            lines.append("\n</details>")
        if len(failing) > max_cases:
            lines.append(f"\n…and {len(failing) - max_cases} more.")
    if verdict.fixed:
        lines.append(f"\nFixed since the baseline: {', '.join(f'`{i}`' for i in verdict.fixed[:10])}")
    if verdict.missing:
        lines.append(f"\nBaseline cases missing from this run: {', '.join(f'`{i}`' for i in verdict.missing[:10])}")
    return "\n".join(lines) + "\n"
