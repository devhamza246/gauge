"""Data models shared by the runner, scorers, storage and server."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ScorerSpec(BaseModel):
    """One scorer to apply: ``type`` plus its parameters (kept flat in YAML)."""
    model_config = ConfigDict(extra="allow")

    type: str
    name: str | None = None
    # A case fails if any scorer with `required: true` (the default) fails.
    required: bool = True

    @property
    def params(self) -> dict[str, Any]:
        return dict(self.model_extra or {})

    @property
    def label(self) -> str:
        return self.name or self.type


class Case(BaseModel):
    id: str
    input: Any
    expected: Any | None = None
    rubric: str | None = None
    context: list[str] | None = None  # reference passages for RAG faithfulness
    tags: list[str] = Field(default_factory=list)
    scorers: list[ScorerSpec] | None = None  # overrides the suite scorers
    metadata: dict[str, Any] = Field(default_factory=dict)


class Thresholds(BaseModel):
    """Regression limits applied by ``gauge compare`` / ``gauge ci``."""
    max_pass_rate_drop: float = 0.02      # absolute, 0.02 = 2 points
    max_new_failures: int = 0             # cases that passed on baseline and fail now
    max_cost_increase: float = 0.25       # relative to baseline total cost
    max_latency_increase: float = 0.50    # relative to baseline p95
    min_pass_rate: float | None = None    # absolute floor, optional


class Suite(BaseModel):
    """Contents of ``gauge.yml``."""
    name: str
    dataset: str
    app: str                              # "package.module:function"
    scorers: list[ScorerSpec] = Field(default_factory=lambda: [ScorerSpec(type="exact_match")])
    concurrency: int = 8
    timeout_s: float = 120.0
    thresholds: Thresholds = Field(default_factory=Thresholds)
    pricing: dict[str, dict[str, float]] = Field(default_factory=dict)  # model -> {input, output} $/1M tokens
    judge: dict[str, Any] = Field(default_factory=dict)                 # provider/model/mode/cassette


class LLMCall(BaseModel):
    model: str = "unknown"
    prompt: Any = None
    output: Any = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    purpose: str = "app"   # "app" or "judge"
    error: str | None = None


class ScoreResult(BaseModel):
    name: str
    passed: bool
    score: float = 0.0
    reason: str = ""
    required: bool = True


class CaseResult(BaseModel):
    case_id: str
    tags: list[str] = Field(default_factory=list)
    input: Any = None
    expected: Any = None
    output: Any = None
    passed: bool = False
    score: float = 0.0
    scores: list[ScoreResult] = Field(default_factory=list)
    calls: list[LLMCall] = Field(default_factory=list)
    latency_ms: float = 0.0
    cost_usd: float = 0.0          # app + judge
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None


class RunSummary(BaseModel):
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    pass_rate: float = 0.0
    cost_usd: float = 0.0
    judge_cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    duration_s: float = 0.0


class RunResult(BaseModel):
    suite: str
    commit: str | None = None
    branch: str | None = None
    pr: str | None = None
    message: str | None = None
    started_at: str | None = None
    source: str = "gauge"
    summary: RunSummary = Field(default_factory=RunSummary)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    cases: list[CaseResult] = Field(default_factory=list)
