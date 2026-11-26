"""Streamlit dashboard: pass rate, cost and latency per commit, plus failing cases with their traces."""
from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from gauge.store import Store

st.set_page_config(page_title="Gauge", page_icon="📏", layout="wide")


@st.cache_resource
def get_store() -> Store:
    return Store(os.environ.get("GAUGE_DB", "sqlite:///gauge.db"))


store = get_store()
st.title("Gauge")
st.caption("Every change is regression-tested; quality, cost and latency are visible per commit.")

suites = store.suites()
if not suites:
    st.info("No runs yet. Upload one with `gauge run --server ...` or `gauge import`.")
    st.stop()

with st.sidebar:
    suite = st.selectbox("Suite", suites)
    rows_all = store.list_runs(suite=suite, limit=300)
    branches = sorted({r.branch or "(none)" for r in rows_all})
    branch = st.selectbox("Branch", ["(all)"] + branches, index=(["(all)"] + branches).index("main") if "main" in branches else 0)
    st.caption("Reads from the Gauge database; uploads need an ingest token.")

rows = [r for r in rows_all if branch == "(all)" or (r.branch or "(none)") == branch]
if not rows:
    st.warning("No runs for this selection.")
    st.stop()

df = pd.DataFrame([{
    "when": r.created_at, "commit": (r.commit or "-")[:8], "branch": r.branch or "-", "message": r.message or "",
    "pass rate": r.pass_rate, "passed": r.passed, "total": r.total, "cost ($)": r.cost_usd,
    "judge cost ($)": r.judge_cost_usd, "p50 (ms)": r.latency_p50_ms, "p95 (ms)": r.latency_p95_ms, "id": r.id,
} for r in rows]).sort_values("when")

latest = df.iloc[-1]
prev = df.iloc[-2] if len(df) > 1 else None
c1, c2, c3, c4 = st.columns(4)
c1.metric("Pass rate", f"{latest['pass rate']:.1%}", None if prev is None else f"{(latest['pass rate'] - prev['pass rate']) * 100:+.1f} pts")
c2.metric("Cases", f"{int(latest['passed'])}/{int(latest['total'])}")
c3.metric("Cost / run", f"${latest['cost ($)']:.4f}", None if prev is None else f"{latest['cost ($)'] - prev['cost ($)']:+.4f}", delta_color="inverse")
c4.metric("Latency p95", f"{latest['p95 (ms)']:.0f} ms", None if prev is None else f"{latest['p95 (ms)'] - prev['p95 (ms)']:+.0f} ms", delta_color="inverse")


chart_df = df.set_index("when")
left, mid, right = st.columns(3)
left.subheader("Pass rate per commit")
left.line_chart(chart_df["pass rate"])
mid.subheader("Cost per run ($)")
mid.bar_chart(chart_df["cost ($)"])
right.subheader("Latency p95 (ms)")
right.line_chart(chart_df["p95 (ms)"])

st.subheader("Runs")
st.dataframe(df.drop(columns=["label", "id"]).sort_values("when", ascending=False), use_container_width=True, hide_index=True)

st.subheader("Failing cases and traces")
options = {f"{r['commit']} · {r['when']:%Y-%m-%d %H:%M} · {r['pass rate']:.1%}": r["id"] for _, r in df.sort_values("when", ascending=False).iterrows()}
chosen = st.selectbox("Run", list(options))
run = Store.to_result(store.get(options[chosen]))
failing = [c for c in run.cases if not c.passed]
st.write(f"{len(failing)} failing of {len(run.cases)} cases")
for case in failing[:50]:
    with st.expander(f"{case.case_id}  ·  {', '.join(case.tags)}"):
        st.markdown(f"**Input:** {case.input}")
        if case.expected is not None:
            st.markdown(f"**Expected:** {case.expected}")
        st.markdown(f"**Got:** {case.output if case.error is None else '(error) ' + case.error}")
        for s in case.scores:
            st.markdown(f"{'✅' if s.passed else '❌'} `{s.name}` — {s.reason}")
        for call in case.calls:
            st.caption(f"{call.purpose} call · {call.model} · {call.input_tokens} in / {call.output_tokens} out · "
                       f"{call.latency_ms:.0f} ms · ${call.cost_usd:.5f}")
            if call.prompt:
                st.code(str(call.prompt)[:1500], language="text")
