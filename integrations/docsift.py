"""Convert a Docsift eval results file (evals/results/*.json) into the generic format `gauge import` reads.

    python integrations/docsift.py PATH/TO/replay.json OUT.json [--commit SHA] [--branch main]
    gauge import OUT.json --server https://... --token ...

A document passes only if the pipeline did not fail and *every* extracted field is correct (a stricter,
document-level view than Docsift's headline field-level accuracy, which it reports separately).
"""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def convert(path: Path, commit: str | None, branch: str, suite: str, message: str | None) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    for doc in data["documents"]:
        wrong = [f["name"] for f in doc.get("fields", []) if not f.get("correct")]
        failed = bool(doc.get("error"))
        cases.append({
            "id": doc["id"],
            "tags": [t for t in (doc.get("type_expected"), doc.get("source"), doc.get("path")) if t],
            "expected": {f["name"]: f.get("expected") for f in doc.get("fields", [])},
            "output": {f["name"]: f.get("actual") for f in doc.get("fields", [])},
            "passed": not failed and not wrong,
            "reason": doc.get("error") or (f"wrong fields: {wrong}" if wrong else "all fields correct"),
            "error": doc.get("error"),
            "latency_ms": doc.get("latency_ms") or 0,
            "cost_usd": float(doc.get("cost_usd") or 0),
            "input_tokens": doc.get("input_tokens") or 0,
            "output_tokens": doc.get("output_tokens") or 0,
            "scorer": "all_fields_correct",
        })
    started = datetime.fromtimestamp(os.stat(path).st_mtime, tz=timezone.utc).isoformat()
    return {"suite": suite, "commit": commit, "branch": branch, "message": message, "started_at": started,
            "source": "docsift-evals", "cases": cases}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("out")
    ap.add_argument("--commit")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--suite", default="docsift-documents")
    ap.add_argument("--message")
    a = ap.parse_args()
    Path(a.out).write_text(json.dumps(convert(Path(a.results), a.commit, a.branch, a.suite, a.message)), encoding="utf-8")
    print(f"wrote {a.out}")
