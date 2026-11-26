import json
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gauge import run_suite
from gauge.cli import main
from gauge.store import Store

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "support_bot" / "gauge.yml"


def _forget_example_modules():
    for mod in [m for m in sys.modules if m.startswith("support_bot")]:
        del sys.modules[mod]


@pytest.fixture(scope="module")
def example_run():
    return run_suite(EXAMPLE, limit=20)


def test_example_suite_runs_and_is_mostly_green(example_run):
    assert example_run.summary.total == 20
    assert example_run.summary.pass_rate >= 0.9
    assert example_run.summary.cost_usd > 0 and example_run.summary.latency_p95_ms > 0


def test_store_roundtrip_and_baseline(tmp_path, example_run):
    store = Store(f"sqlite:///{tmp_path / 'g.db'}")
    run = example_run.model_copy(update={"branch": "main", "commit": "abc"})
    run_id = store.save(run)
    again = Store.to_result(store.get(run_id))
    assert again.summary.passed == run.summary.passed and len(again.cases) == 20
    assert store.suites() == ["support-bot"]
    assert store.baseline("support-bot", "main").id == run_id
    assert store.baseline("support-bot", "nope") is None


def test_api_auth_upload_and_query(tmp_path, example_run, monkeypatch):
    monkeypatch.setenv("GAUGE_DB", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("GAUGE_TOKEN", "secret")
    import gauge.server.api as api
    api._store = None
    client = TestClient(api.app)
    body = example_run.model_copy(update={"branch": "main"}).model_dump(mode="json")
    assert client.post("/api/runs", json=body).status_code == 401
    assert client.post("/api/runs", json=body, headers={"authorization": "Bearer wrong"}).status_code == 401
    ok = client.post("/api/runs", json=body, headers={"authorization": "Bearer secret"})
    assert ok.status_code == 200
    run_id = ok.json()["id"]
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/api/suites").json() == ["support-bot"]
    listing = client.get("/api/runs", params={"suite": "support-bot"}).json()
    assert listing[0]["id"] == run_id and listing[0]["total"] == 20
    assert len(client.get(f"/api/runs/{run_id}").json()["cases"]) == 20
    assert client.get("/api/baseline", params={"suite": "support-bot"}).status_code == 200
    assert client.get("/api/baseline", params={"suite": "zzz"}).status_code == 404
    empty = client.post("/api/runs", json={**body, "cases": []}, headers={"authorization": "Bearer secret"})
    assert empty.status_code == 422
    api._store = None


def test_cli_baseline_then_ci_passes_then_degraded_prompt_fails(tmp_path, monkeypatch):
    work = tmp_path / "ex"
    shutil.copytree(EXAMPLE.parent, work / "support_bot", ignore=shutil.ignore_patterns("__pycache__"))
    _forget_example_modules()
    monkeypatch.syspath_prepend(str(work))
    cfg = work / "support_bot" / "gauge.yml"
    baseline = tmp_path / "baseline.json"
    assert main(["baseline", "-c", str(cfg), "--out", str(baseline)]) == 0
    assert json.loads(baseline.read_text())["summary"]["total"] == 100

    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert main(["ci", "-c", str(cfg), "--baseline", str(baseline)]) == 0
    assert "PASSED" in summary.read_text()

    prompts = work / "support_bot" / "prompts.py"
    prompts.write_text(prompts.read_text().replace("Reply with only a JSON object: ", "Reply with the category and priority: "))
    _forget_example_modules()
    summary.write_text("")
    assert main(["ci", "-c", str(cfg), "--baseline", str(baseline)]) == 1
    text = summary.read_text()
    assert "REGRESSION DETECTED" in text and "valid_json" in text and "pass rate fell" in text
    _forget_example_modules()


def test_cli_import_and_bad_input(tmp_path):
    data = {"suite": "ext", "commit": "c1", "branch": "main",
            "cases": [{"id": "1", "passed": True, "latency_ms": 10, "cost_usd": 0.01},
                      {"id": "2", "passed": False, "reason": "wrong"}]}
    f = tmp_path / "ext.json"
    f.write_text(json.dumps(data))
    db = f"sqlite:///{tmp_path / 'i.db'}"
    assert main(["import", str(f), "--db", db]) == 0
    assert Store(db).baseline("ext", "main").pass_rate == 0.5
    assert main(["run", "-c", str(tmp_path / "missing.yml")]) == 2
