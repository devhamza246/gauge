"""Convert a Stacklight eval results file (evals/results/{hybrid,naive}.json) into the format `gauge import` reads.

    python integrations/stacklight.py PATH/TO/hybrid.json OUT.json [--suite stacklight-hybrid]

Case verdict (defined here, not by Stacklight): an answerable question passes if the gold source was retrieved
(`hit`), the system did not abstain, and faithfulness (when measured) is at least 0.7; an unanswerable question
passes if the system abstained.
"""
import argparse
import json
from pathlib import Path

FAITHFULNESS_MIN = 0.7


def convert(path: Path, suite: str, branch: str, message: str | None) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    for row in data["rows"]:
        if row["answerable"]:
            faith = row.get("faithfulness")
            problems = []
            if not row.get("hit"):
                problems.append("gold source not retrieved")
            if row.get("abstained"):
                problems.append("abstained on an answerable question")
            if faith is not None and faith < FAITHFULNESS_MIN:
                problems.append(f"faithfulness {faith:.2f} < {FAITHFULNESS_MIN}")
            passed, reason = not problems, "; ".join(problems) or "retrieved, answered and faithful"
        else:
            passed = bool(row.get("abstained"))
            reason = "abstained" if passed else "answered an unanswerable question"
        cases.append({
            "id": row["id"], "tags": ["answerable" if row["answerable"] else "unanswerable"],
            "input": row["question"], "output": row.get("answer") or "(abstained)", "passed": passed, "reason": reason,
            "latency_ms": row.get("latency_ms") or 0, "cost_usd": float(row.get("cost_usd") or 0),
            "scorer": "retrieval_answer_abstention"})
    return {"suite": suite, "commit": data.get("git_commit"), "branch": branch, "message": message,
            "started_at": data.get("timestamp"), "source": "stacklight-evals", "cases": cases}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("out")
    ap.add_argument("--suite")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--message")
    a = ap.parse_args()
    suite = a.suite or f"stacklight-{json.loads(Path(a.results).read_text())['pipeline']}"
    Path(a.out).write_text(json.dumps(convert(Path(a.results), suite, a.branch, a.message)), encoding="utf-8")
    print(f"wrote {a.out} ({suite})")
