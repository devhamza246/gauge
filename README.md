# Gauge

**Make your LLM feature measurable: quality scores, regressions caught in CI, cost and latency per commit.**

Gauge is a small Python library, CLI and self-hostable dashboard. You describe your cases in YAML/JSONL, point it at
your app, and it runs the cases in parallel, traces every LLM call (prompt, output, tokens, latency, cost), scores the
results, stores them per commit and fails the pull request when quality, cost or latency regress against `main`.

**Live:** [dashboard](https://gauge.hamzacodes.dev) (Docsift and Stacklight results, plus the example app's
passing `main` run and a failing PR run) · [the failing demo PR](https://github.com/devhamza246/gauge/pull/1)

## Quickstart

```bash
pip install git+https://github.com/devhamza246/gauge   # not on PyPI yet
cat > gauge.yml <<'EOF'
name: my-bot
dataset: cases.jsonl              # {"id": "1", "input": "...", "expected": "..."} per line
app: my_package.bot:answer        # def answer(input) -> output
scorers: [{type: exact_match}]
EOF
gauge baseline && git add gauge-baseline.json   # commit once; CI compares every change against it
gauge ci                                         # run + compare; exit 1 on regression
```

Add the gate to GitHub Actions:

```yaml
- uses: actions/checkout@v4
- uses: devhamza246/gauge@main
  with: { config: gauge.yml }
```

## What a failing PR looks like

A pull request that degrades the prompt in [`examples/support_bot/prompts.py`](examples/support_bot/prompts.py) fails the
`eval-gate` job and posts this to the PR (also written to the job summary):

> **❌ Gauge: support-bot: REGRESSION DETECTED** · pass rate 90.0% to 0.0% · 90 cases that passed on the baseline now fail.
> Each failing case lists its input, expected value, the model output, every scorer's reason and the traced call
> (tokens, latency, cost, prompt).

See the demo pull request linked from the repository, and `tests/test_store_api_cli.py::test_cli_baseline_then_ci_passes_then_degraded_prompt_fails`,
which reproduces it end to end.

## Concepts

| Piece | What it does |
|---|---|
| **Dataset** | YAML or JSONL. Each case has `id`, `input`, optional `expected`, `rubric`, `context`, `tags`. |
| **App** | Any `def app(input) -> output`. Return `gauge.AppOutput(output, context=[...])` to expose retrieved RAG passages. |
| **Tracing** | `@observe(model=...)`, `wrap_anthropic(client)`, `wrap_openai(client)` or `record_call(...)` capture prompt, output, tokens, latency and cost per call. |
| **Scorers** | Deterministic first: `exact_match`, `contains`, `not_contains`, `regex`, `json_schema`, `field_match`, `max_length`. Then `llm_judge` (rubric) and `rag_faithfulness` (fraction of claims supported by the context). Custom: `{type: python, path: "pkg.mod:fn"}`. |
| **Judge cassettes** | Judge replies are recorded to a JSONL cassette and replayed in CI, so judged suites are free, offline and deterministic. `gauge ci` defaults to replay. |
| **Storage** | SQLAlchemy: SQLite locally, PostgreSQL when self-hosted. One row per run, one per case with the full trace. |
| **Gate** | `gauge ci` compares with a committed baseline file or the latest `main` run on your server. Limits (`thresholds:` in gauge.yml): pass-rate drop, new failures, cost increase, p95 latency increase, optional absolute floor. |
| **Dashboard** | Streamlit: pass rate, cost and p95 latency per commit, run table, failing cases with their traces. |

Prices live in `gauge/pricing.py` and are overridable per suite (`pricing:` in gauge.yml, USD per million tokens).

## CLI

```
gauge run        run the suite, print results (--out, --db, --server, --tag, --limit)
gauge baseline   write gauge-baseline.json
gauge ci         run, compare with the baseline, write the PR report, exit 1 on regression
gauge compare    compare two results files
gauge calibrate  measure how often the judge agrees with human labels
gauge import     import results from another harness (used for Docsift and Stacklight)
gauge serve      run the ingest/query API
```

## Self-hosting

```bash
cp .env.example .env              # set POSTGRES_PASSWORD and GAUGE_TOKEN
docker compose --env-file .env up -d --build
# CI uploads runs and fetches the main baseline:
gauge ci --server https://your-gauge-host --token "$GAUGE_TOKEN"
```

`POST /api/runs` needs the bearer token; reads are open unless `GAUGE_PUBLIC_READ=0`.

## What is and is not proven here

Honest status, so you can weigh the claims:

- **Tested:** 23 tests cover the scorers, judge cassettes, runner, regression logic, storage, API auth and the CLI,
  including a degrade-the-prompt run that must fail the gate.
- **The example app uses a simulated model.** `examples/support_bot` runs a deterministic keyword "LLM" so the demo is
  free and reproducible. Its 100-case suite finishes in well under a second, so the "100 cases in under 5 minutes"
  target is only demonstrated for that simulation; real runtime depends on your model's latency and your concurrency.
  Swap in `wrap_anthropic` / `wrap_openai` to evaluate a real model.
- **Real data:** results from the Docsift (40 documents) and Stacklight (53 questions) eval harnesses are imported into
  the hosted dashboard with `gauge import`. They are those projects' own numbers, not re-measured by Gauge.
- **Judge calibration is not yet measured.** [`examples/calibration`](examples/calibration) holds 20 human-labelled
  cases and the command to run them (needs an API key). Until that has been run and committed, treat judge scores as
  unvalidated; the target is at least 90% agreement.
- **Out of scope for v0.1:** hosted SaaS, human annotation UI, distributed tracing, alerting, prompt versioning,
  support for every provider (Anthropic and OpenAI-compatible judges; Anthropic and OpenAI client wrappers).

## Development

```bash
pip install -e ".[server,dev]"
pytest
```

MIT licensed.
