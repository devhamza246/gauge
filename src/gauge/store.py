"""SQLAlchemy storage: SQLite by default, PostgreSQL in production (same code)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .models import CaseResult, RunResult, RunSummary, Thresholds


class Base(DeclarativeBase):
    pass


class RunRow(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    suite: Mapped[str] = mapped_column(String(120), index=True)
    commit: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    branch: Mapped[str | None] = mapped_column(String(200), index=True, nullable=True)
    pr: Mapped[str | None] = mapped_column(String(20), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(40), default="gauge")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    total: Mapped[int] = mapped_column(Integer)
    passed: Mapped[int] = mapped_column(Integer)
    pass_rate: Mapped[float] = mapped_column(Float)
    cost_usd: Mapped[float] = mapped_column(Float)
    judge_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_p50_ms: Mapped[float] = mapped_column(Float, default=0.0)
    latency_p95_ms: Mapped[float] = mapped_column(Float, default=0.0)
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    thresholds: Mapped[dict] = mapped_column(JSON, default=dict)
    cases: Mapped[list["CaseRow"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class CaseRow(Base):
    __tablename__ = "case_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[str] = mapped_column(String(200), index=True)
    passed: Mapped[bool] = mapped_column(Boolean)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[dict] = mapped_column(JSON)  # the full CaseResult (input, output, scores, calls)
    run: Mapped[RunRow] = relationship(back_populates="cases")


class Store:
    def __init__(self, url: str = "sqlite:///gauge.db"):
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        self.engine = create_engine(url, future=True, pool_pre_ping=True)
        Base.metadata.create_all(self.engine)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    def session(self) -> Session:
        return self._sessions()

    def save(self, run: RunResult) -> str:
        s = run.summary
        created = datetime.fromisoformat(run.started_at) if run.started_at else datetime.now(timezone.utc)
        row = RunRow(
            suite=run.suite, commit=run.commit, branch=run.branch, pr=run.pr, message=run.message,
            source=run.source, created_at=created, total=s.total, passed=s.passed, pass_rate=s.pass_rate,
            cost_usd=s.cost_usd, judge_cost_usd=s.judge_cost_usd, input_tokens=s.input_tokens,
            output_tokens=s.output_tokens, latency_p50_ms=s.latency_p50_ms, latency_p95_ms=s.latency_p95_ms,
            duration_s=s.duration_s, errors=s.errors, thresholds=run.thresholds.model_dump())
        row.cases = [CaseRow(case_id=c.case_id, passed=c.passed, score=c.score, latency_ms=c.latency_ms,
                             cost_usd=c.cost_usd, error=c.error, data=c.model_dump(mode="json"))
                     for c in run.cases]
        with self.session() as session:
            session.add(row)
            session.commit()
            return row.id

    @staticmethod
    def to_result(row: RunRow, with_cases: bool = True) -> RunResult:
        return RunResult(
            suite=row.suite, commit=row.commit, branch=row.branch, pr=row.pr, message=row.message,
            started_at=row.created_at.isoformat(), source=row.source,
            summary=RunSummary(
                total=row.total, passed=row.passed, failed=row.total - row.passed, errors=row.errors,
                pass_rate=row.pass_rate, cost_usd=row.cost_usd, judge_cost_usd=row.judge_cost_usd,
                input_tokens=row.input_tokens, output_tokens=row.output_tokens, latency_p50_ms=row.latency_p50_ms,
                latency_p95_ms=row.latency_p95_ms, duration_s=row.duration_s),
            thresholds=Thresholds.model_validate(row.thresholds or {}),
            cases=[CaseResult.model_validate(c.data) for c in row.cases] if with_cases else [])

    def get(self, run_id: str) -> RunRow | None:
        with self.session() as session:
            row = session.get(RunRow, run_id)
            if row:
                _ = row.cases  # load before the session closes
            return row

    def list_runs(self, suite: str | None = None, branch: str | None = None, limit: int = 100) -> list[RunRow]:
        query = select(RunRow).order_by(RunRow.created_at.desc()).limit(limit)
        if suite:
            query = query.where(RunRow.suite == suite)
        if branch:
            query = query.where(RunRow.branch == branch)
        with self.session() as session:
            return list(session.scalars(query))

    def suites(self) -> list[str]:
        with self.session() as session:
            return sorted(session.scalars(select(RunRow.suite).distinct()))

    def baseline(self, suite: str, branch: str = "main") -> RunRow | None:
        rows = self.list_runs(suite=suite, branch=branch, limit=1)
        if not rows:
            return None
        return self.get(rows[0].id)
