"""LLM-backed scorers. They need a configured Judge; use cassettes to keep CI free and deterministic."""
from __future__ import annotations

import json
from typing import Any

from ..judge import JudgeError, parse_json_reply
from ..models import Case, ScoreResult
from . import ScoreContext, register

JUDGE_SYSTEM = (
    "You are a strict, consistent evaluator of an AI system's output. Judge only against the rubric. "
    "Reply with a single JSON object and nothing else: "
    '{"pass": true|false, "score": <number 0 to 1>, "reason": "<one short sentence>"}'
)

FAITHFULNESS_SYSTEM = (
    "You check whether an answer is faithful to the provided context. Split the answer into its distinct factual "
    "claims. A claim is supported only if the context states it or directly implies it; general knowledge does not "
    "count. Reply with a single JSON object and nothing else: "
    '{"claims": [{"claim": "<text>", "supported": true|false}], "reason": "<one short sentence>"}'
)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


def _need_judge(ctx: ScoreContext) -> None:
    if ctx.judge is None:
        raise JudgeError("this scorer needs a judge: add a `judge:` section to gauge.yml")


@register("llm_judge")
def llm_judge(case: Case, output: Any, ctx: ScoreContext) -> ScoreResult:
    _need_judge(ctx)
    rubric = ctx.params.get("rubric") or case.rubric
    if not rubric:
        return ScoreResult(name=ctx.spec.label, passed=False, reason="no `rubric` on the scorer or the case",
                           required=ctx.spec.required)
    user = f"Rubric:\n{rubric}\n\nInput:\n{_text(case.input)}\n\n"
    if case.expected is not None:
        user += f"Reference answer:\n{_text(case.expected)}\n\n"
    user += f"Output to judge:\n{_text(output)}"
    reply = parse_json_reply(ctx.judge.ask(JUDGE_SYSTEM, user, model=ctx.params.get("model")))
    score = float(reply.get("score", 1.0 if reply.get("pass") else 0.0))
    passed = bool(reply.get("pass", False)) and score >= float(ctx.params.get("min_score", 0.0))
    return ScoreResult(name=ctx.spec.label, passed=passed, score=score,
                       reason=str(reply.get("reason", ""))[:300], required=ctx.spec.required)


@register("rag_faithfulness")
def rag_faithfulness(case: Case, output: Any, ctx: ScoreContext) -> ScoreResult:
    """Fraction of the answer's claims supported by the context. Uses the passages the app reported it
    retrieved (AppOutput.context); falls back to the case's reference `context`."""
    _need_judge(ctx)
    passages = ctx.retrieved or case.context
    if not passages:
        return ScoreResult(name=ctx.spec.label, passed=False,
                           reason="no context: the app must return AppOutput(context=[...]) or the case needs `context`",
                           required=ctx.spec.required)
    user = "Context:\n" + "\n---\n".join(passages) + f"\n\nAnswer to check:\n{_text(output)}"
    reply = parse_json_reply(ctx.judge.ask(FAITHFULNESS_SYSTEM, user, model=ctx.params.get("model")))
    claims = reply.get("claims") or []
    if not claims:
        return ScoreResult(name=ctx.spec.label, passed=False, reason="judge returned no claims",
                           required=ctx.spec.required)
    supported = [c for c in claims if c.get("supported")]
    score = len(supported) / len(claims)
    unsupported = [c.get("claim", "")[:80] for c in claims if not c.get("supported")]
    return ScoreResult(
        name=ctx.spec.label, passed=score >= float(ctx.params.get("threshold", 0.8)), score=score,
        reason=(f"{len(supported)}/{len(claims)} claims supported" +
                (f"; unsupported: {unsupported}" if unsupported else "")),
        required=ctx.spec.required)
