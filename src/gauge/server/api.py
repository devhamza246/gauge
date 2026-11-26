"""Ingest and query API. Writes need GAUGE_TOKEN; reads are open unless GAUGE_PUBLIC_READ=0."""
from __future__ import annotations

import hmac
import os

from fastapi import Depends, FastAPI, Header, HTTPException

from ..models import RunResult
from ..store import RunRow, Store

app = FastAPI(title="Gauge", version="0.1.0")
_store: Store | None = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store(os.environ.get("GAUGE_DB", "sqlite:///gauge.db"))
    return _store


def _token_ok(authorization: str | None) -> bool:
    expected = os.environ.get("GAUGE_TOKEN", "")
    if not expected or not authorization or not authorization.lower().startswith("bearer "):
        return False
    return hmac.compare_digest(authorization[7:].strip(), expected)


def require_write(authorization: str | None = Header(default=None)) -> None:
    if not _token_ok(authorization):
        raise HTTPException(status_code=401, detail="a valid bearer token is required to upload runs")


def require_read(authorization: str | None = Header(default=None)) -> None:
    if os.environ.get("GAUGE_PUBLIC_READ", "1") != "0":
        return
    if not _token_ok(authorization):
        raise HTTPException(status_code=401, detail="a valid bearer token is required")


def _summary(row: RunRow) -> dict:
    return {
        "id": row.id, "suite": row.suite, "commit": row.commit, "branch": row.branch, "pr": row.pr,
        "message": row.message, "source": row.source, "created_at": row.created_at.isoformat(),
        "total": row.total, "passed": row.passed, "pass_rate": row.pass_rate, "cost_usd": row.cost_usd,
        "judge_cost_usd": row.judge_cost_usd, "latency_p50_ms": row.latency_p50_ms,
        "latency_p95_ms": row.latency_p95_ms, "duration_s": row.duration_s,
    }


@app.get("/healthz")
def healthz() -> dict:
    store().suites()  # touches the database
    return {"status": "ok"}


@app.post("/api/runs", dependencies=[Depends(require_write)])
def upload(run: RunResult) -> dict:
    if not run.cases:
        raise HTTPException(status_code=422, detail="a run needs at least one case")
    return {"id": store().save(run)}


@app.get("/api/suites", dependencies=[Depends(require_read)])
def suites() -> list[str]:
    return store().suites()


@app.get("/api/runs", dependencies=[Depends(require_read)])
def runs(suite: str | None = None, branch: str | None = None, limit: int = 100) -> list[dict]:
    return [_summary(r) for r in store().list_runs(suite, branch, min(max(limit, 1), 500))]


@app.get("/api/runs/{run_id}", dependencies=[Depends(require_read)])
def run_detail(run_id: str) -> dict:
    row = store().get(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="run not found")
    return Store.to_result(row).model_dump(mode="json")


@app.get("/api/baseline", dependencies=[Depends(require_read)])
def baseline(suite: str, branch: str = "main") -> dict:
    row = store().baseline(suite, branch)
    if row is None:
        raise HTTPException(status_code=404, detail="no baseline run for this suite and branch")
    return Store.to_result(row).model_dump(mode="json")
