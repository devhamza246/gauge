import json

import pytest

from gauge.judge import Judge, JudgeError, JudgeReply
from gauge.models import Case, ScorerSpec
from gauge.scorers import ScoreContext, get_scorer


def score(spec: dict, case: Case, output, judge=None, retrieved=None):
    s = ScorerSpec.model_validate(spec)
    return get_scorer(s)(case, output, ScoreContext(spec=s, judge=judge, retrieved=retrieved))


def test_exact_match_normalizes():
    case = Case(id="a", input="x", expected="Paris.")
    assert score({"type": "exact_match"}, case, "  paris ").passed
    assert not score({"type": "exact_match"}, case, "London").passed
    assert not score({"type": "exact_match"}, Case(id="b", input="x"), "x").passed


def test_exact_match_structured():
    case = Case(id="a", input="x", expected={"k": 1})
    assert score({"type": "exact_match"}, case, {"k": 1}).passed
    assert not score({"type": "exact_match"}, case, {"k": 2}).passed


def test_contains_and_not_contains():
    case = Case(id="a", input="x")
    assert score({"type": "contains", "values": ["refund", "30 days"]}, case, "Refund within 30 days").passed
    r = score({"type": "contains", "values": ["refund", "week"]}, case, "refund only")
    assert not r.passed and "week" in r.reason
    assert score({"type": "contains", "values": ["a", "b"], "any": True}, case, "only a").passed
    assert not score({"type": "not_contains", "values": ["password"]}, case, "your Password is").passed


def test_regex_and_length():
    case = Case(id="a", input="x")
    assert score({"type": "regex", "pattern": r"\d{3}-\d{4}"}, case, "call 555-1234").passed
    assert not score({"type": "max_length", "chars": 5}, case, "too long").passed


def test_json_schema():
    spec = {"type": "json_schema", "schema": {"type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}}}}
    case = Case(id="a", input="x")
    assert score(spec, case, {"n": 3}).passed
    assert score(spec, case, '{"n": 3}').passed
    bad = score(spec, case, {"n": "three"})
    assert not bad.passed and "n" in bad.reason
    assert "not valid JSON" in score(spec, case, "plain text").reason


def test_field_match_partial_score():
    case = Case(id="a", input="x", expected={"a": 1, "b": 2})
    r = score({"type": "field_match", "fields": ["a", "b"]}, case, {"a": 1, "b": 9})
    assert not r.passed and r.score == 0.5 and "b" in r.reason
    assert score({"type": "field_match", "fields": ["a", "b"], "threshold": 0.5}, case, {"a": 1, "b": 9}).passed


def fake_judge(reply: str) -> Judge:
    return Judge(provider=lambda model, system, user: JudgeReply(reply, 100, 20), mode="live")


def test_llm_judge_parses_reply():
    case = Case(id="a", input="q", rubric="Be polite")
    j = fake_judge('Sure.\n{"pass": true, "score": 0.9, "reason": "polite"}')
    r = score({"type": "llm_judge"}, case, "hello", judge=j)
    assert r.passed and r.score == 0.9 and r.reason == "polite"
    r = score({"type": "llm_judge", "min_score": 0.95}, case, "hello", judge=j)
    assert not r.passed


def test_llm_judge_requires_judge_and_rubric():
    with pytest.raises(JudgeError):
        score({"type": "llm_judge"}, Case(id="a", input="q", rubric="r"), "x")
    r = score({"type": "llm_judge"}, Case(id="a", input="q"), "x", judge=fake_judge("{}"))
    assert not r.passed and "rubric" in r.reason


def test_rag_faithfulness_fraction():
    reply = json.dumps({"claims": [{"claim": "a", "supported": True}, {"claim": "b", "supported": True},
                                   {"claim": "c", "supported": False}], "reason": ""})
    case = Case(id="a", input="q", context=["doc"])
    r = score({"type": "rag_faithfulness", "threshold": 0.6}, case, "ans", judge=fake_judge(reply))
    assert r.passed and abs(r.score - 2 / 3) < 1e-9 and "unsupported" in r.reason
    assert not score({"type": "rag_faithfulness"}, case, "ans", judge=fake_judge(reply)).passed
    r = score({"type": "rag_faithfulness"}, Case(id="b", input="q"), "ans", judge=fake_judge(reply))
    assert not r.passed and "no context" in r.reason


def test_judge_cassette_replay(tmp_path):
    calls = []

    def provider(model, system, user):
        calls.append(user)
        return JudgeReply('{"pass": true, "score": 1, "reason": "ok"}', 10, 5)

    cassette = tmp_path / "c.jsonl"
    Judge(provider=provider, mode="auto", cassette=cassette).ask("s", "u")
    assert len(calls) == 1
    replay = Judge(provider=None, mode="replay", cassette=cassette)
    assert "pass" in replay.ask("s", "u")
    with pytest.raises(JudgeError):
        replay.ask("s", "different input")
