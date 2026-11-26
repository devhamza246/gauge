"""Judge calibration: how often does the LLM judge agree with human labels?"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .judge import Judge
from .models import Case, ScorerSpec
from .scorers import ScoreContext, get_scorer


@dataclass
class Row:
    case_id: str
    judge_pass: bool
    human_pass: bool
    score: float
    reason: str

    @property
    def agrees(self) -> bool:
        return self.judge_pass == self.human_pass


def calibrate(labels_path: str | Path, spec: ScorerSpec, judge: Judge) -> list[Row]:
    """Labels: JSONL of {"id", "output", "human_pass", and "input"/"expected"/"rubric"/"context" as the scorer needs}."""
    rows: list[Row] = []
    for line in Path(labels_path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        case = Case(id=item["id"], input=item.get("input"), expected=item.get("expected"),
                    rubric=item.get("rubric"), context=item.get("context"))
        result = get_scorer(spec)(case, item["output"], ScoreContext(spec=spec, judge=judge))
        rows.append(Row(item["id"], result.passed, bool(item["human_pass"]), result.score, result.reason))
    return rows


def agreement(rows: list[Row]) -> float:
    return sum(r.agrees for r in rows) / len(rows) if rows else 0.0


def render_report(rows: list[Row], judge_model: str, target: float) -> str:
    rate = agreement(rows)
    positives = [r for r in rows if r.human_pass]
    negatives = [r for r in rows if not r.human_pass]
    lines = [
        f"# Judge calibration: {judge_model}", "",
        f"**Agreement with human labels: {sum(r.agrees for r in rows)}/{len(rows)} = {rate:.0%}** "
        f"(target at least {target:.0%}: {'MET' if rate >= target else 'NOT MET'})", "",
        f"- on cases a human marked faithful: {sum(r.agrees for r in positives)}/{len(positives)}",
        f"- on cases a human marked unfaithful: {sum(r.agrees for r in negatives)}/{len(negatives)}", "",
        "| case | human | judge | agree | judge's reason |", "|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r.case_id} | {'pass' if r.human_pass else 'fail'} | {'pass' if r.judge_pass else 'fail'} | "
                     f"{'yes' if r.agrees else '**NO**'} | {r.reason[:110].replace('|', '/')} |")
    lines += ["", "Labels were written by hand for this repository (20 short synthetic cases), so this measures agreement "
              "on easy, clear-cut examples and should not be read as accuracy on your own data.", ""]
    return "\n".join(lines)
