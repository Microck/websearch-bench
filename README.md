# Web Search Benchmark Kit

Web Search Benchmark Kit compares ranked web-search results from several
providers under a shared query set. It measures retrieval quality, result
depth, latency, reliability, and ranking stability. It does not generate
answers or crawl result pages during the retrieval phase.

The runner currently supports Exa, Kagi subscriber-session search, Context.dev,
Octen, Parallel.ai, Perplexity, Brave Search, Tavily, and Keenable (opt-in with `--providers keenable`). The benchmark design and metric definitions
are documented in [the benchmark design](docs/benchmark-design.md).

## Repository layout

- `runners/` contains retrieval and URL-health
  entry points.
- `analysis/` contains scoring, merging, and chart
  generation modules.
- `data/` contains the benchmark query catalog.
- `templates/` contains judging and run schemas.
- `docs/` contains the benchmark design and chart specification.
- `tests/` contains contract tests.
- `results/` contains a sanitized result release with aggregate tables,
  charts, and per-query raw data for transparency.
- `runs/` is intentionally local-only. It can contain raw requests, raw
  responses, provider identifiers, and other experiment artifacts.
- `scratch/` is local-only exploratory work.

Full benchmark runs are not part of the repository. The curated result release
contains aggregate tables, selected charts, and the underlying per-query
result data (URLs, ranks, titles, snippets, relevance grades, and per-request
latencies). Billing data, provider request identifiers, absolute machine
paths, raw JSON API responses, and AI judging prompts are stripped from the
published data.

## Quick start

Use Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Set the provider credentials for the services you want to test. The runner
reads these variables and never stores their values:

```bash
export EXA_API_KEY=...
export CONTEXT_DEV_API_KEY=...
export OCTEN_API_KEY=...
export PARALLEL_API_KEY=...
export PERPLEXITY_API_KEY=...
export BRAVE_API_KEY=...
export TAVILY_API_KEY=...
export KEENABLE_API_KEY=...   # optional; Keenable also works without a key
```

Keenable is not in the default provider set. To include it in a run:

```bash
python runners/run_benchmark.py \
  --queries data/queries.csv \
  --out runs/local-run \
  --providers keenable
```

Kagi uses the installed subscriber-session `kagi` CLI path. It is not read
from a value committed to this repository.

Run retrieval into the ignored local `runs/` directory:

```bash
python runners/run_benchmark.py \
  --queries data/queries.csv \
  --out runs/local-run \
  --include-reference-queries
```

Run only the local harnesses (Codex and Claude Code) with no API-key providers:

```bash
python runners/run_ai_harnesses.py \
  --queries data/queries.csv \
  --out runs/ai-harness-run \
  --providers codex,claude_code \
  --max-queries 1 \
  --no-include-reference-queries
```

Use Codex model and Claude/CLI-proxy auth settings:

```bash
python runners/run_ai_harnesses.py \
  --queries data/queries.csv \
  --out runs/ai-harness-run-proxied \
  --providers codex,claude_code \
  --codex-model gpt-3.5-codex-spark \
  --claude-model opus-4.1 \
  --claude-base-url "$CLIPROXYAPI_BASE_URL" \
  --claude-api-key "$CLIPROXYAPI_API_KEY"
```

If you omit `--claude-base-url`/`--claude-api-key`, the runner falls back to
`CLIPROXYAPI_BASE_URL` and `CLIPROXYAPI_API_KEY` from environment when
present.

Generate operational charts from a local run:

```bash
python analysis/make_operational_charts.py \
  --run-dir runs/local-run \
  --out runs/local-run/charts
```

Build the standalone Octen anchor report:

```bash
python analysis/build_benchmark_report.py \
  --run-dir runs/octen-anchors-20260809 \
  --out docs/benchmark-report.html
```

Open `docs/benchmark-report.html` directly or serve `docs/` with a local HTTP
server. The report contains aggregate scores and query-level match ranks, not
raw provider responses.

Run the benchmark tests:

```bash
python -m pytest tests/test_run_benchmark.py
```

## Data and interpretation

The benchmark query set and templates are included so the measurement design
can be inspected. The results directory contains per-query, per-provider
result data (URLs, ranks, snippets, relevance grades) so scores can be
verified. Raw JSON API responses stay outside the repository. The published
quality tables are aggregate diagnostics. The metadata heuristic and blind
AI-card view are not human landing-page judgments.

## License

The code is released under the MIT License. See [LICENSE](LICENSE).
