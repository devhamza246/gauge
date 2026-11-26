import json
from pathlib import Path

import httpx
import pytest

from gauge.calibrate import agreement, calibrate, render_report
from gauge.cli import main
from gauge.judge import Judge, JudgeError, JudgeReply, anthropic_provider
from gauge.models import ScorerSpec

LABELS = Path(__file__).resolve().parents[1] / "examples" / "calibration" / "labels.jsonl"
CONFIG = Path(__file__).resolve().parents[1] / "examples" / "calibration" / "gauge.yml"


def test_label_file_is_well_formed():
    rows = [json.loads(line) for line in LABELS.read_text().splitlines() if line.strip()]
    assert len(rows) == 20
    assert len({r["id"] for r in rows}) == 20
    assert sum(r["human_pass"] for r in rows) == 10  # balanced: 10 faithful, 10 unfaithful
    assert all(r["context"] and r["output"] for r in rows)


def oracle_judge() -> Judge:
    """A judge that knows the human labels (to test the plumbing, not to claim any accuracy)."""
    truth = {json.loads(l)["output"]: json.loads(l)["human_pass"] for l in LABELS.read_text().splitlines() if l.strip()}

    def provider(model, system, user):
        answer = user.split("Answer to check:\n", 1)[1]
        ok = truth[answer]
        return JudgeReply(json.dumps({"claims": [{"claim": "x", "supported": ok}], "reason": "oracle"}), 50, 10)

    return Judge(provider=provider, mode="live")


def test_calibrate_computes_agreement_and_report():
    spec = ScorerSpec(type="rag_faithfulness", threshold=0.8)
    rows = calibrate(LABELS, spec, oracle_judge())
    assert len(rows) == 20 and agreement(rows) == 1.0
    report = render_report(rows, "test-judge", 0.9)
    assert "20/20 = 100%" in report and "MET" in report and "NOT MET" not in report

    def always_pass(model, system, user):
        return JudgeReply(json.dumps({"claims": [{"claim": "x", "supported": True}]}), 1, 1)

    rows = calibrate(LABELS, spec, Judge(provider=always_pass, mode="live"))
    assert agreement(rows) == 0.5
    assert "NOT MET" in render_report(rows, "lazy", 0.9)


def test_cli_calibrate_without_key_is_a_clean_error(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = tmp_path / "gauge.yml"
    cfg.write_text(CONFIG.read_text().replace("../support_bot/dataset.jsonl", str(CONFIG.parent.parent / "support_bot" / "dataset.jsonl")))
    code = main(["calibrate", "-c", str(cfg), "--labels", str(LABELS), "--judge-mode", "live"])
    assert code == 2
    assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().err


def test_cli_calibrate_replay_without_cassette_explains_what_to_do(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = tmp_path / "gauge.yml"
    cfg.write_text("name: j\ndataset: d.jsonl\napp: x:y\njudge: {provider: anthropic, cassette: none.jsonl}\n"
                   "scorers: [{type: rag_faithfulness}]\n")
    assert main(["calibrate", "-c", str(cfg), "--labels", str(LABELS), "--judge-mode", "replay"]) == 2
    assert "no recorded judge response" in capsys.readouterr().err


def test_anthropic_provider_retries_without_temperature(monkeypatch):
    seen = []

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.append(dict(json))
        if "temperature" in json:
            return httpx.Response(400, text='{"error": "temperature is not supported for this model"}',
                                  request=httpx.Request("POST", url))
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}],
                                         "usage": {"input_tokens": 7, "output_tokens": 2}},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    reply = anthropic_provider(api_key="k")("claude-haiku-5-5", "sys", "user")
    assert reply.text == "ok" and (reply.input_tokens, reply.output_tokens) == (7, 2)
    assert "temperature" in seen[0] and "temperature" not in seen[1]


def test_anthropic_provider_reports_bad_key(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(401, text="nope", request=httpx.Request("POST", "http://x")))
    with pytest.raises(JudgeError, match="rejected the key"):
        anthropic_provider(api_key="bad")("m", "s", "u")
