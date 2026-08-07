# Web Search Benchmark Kit

Web Search Benchmark Kit compares ranked web-search results from several
providers under a shared query set. It measures retrieval quality, result
depth, latency, reliability, and ranking stability. It does not generate
answers or crawl result pages during the retrieval phase.

The runner currently supports Exa, Kagi subscriber-session search, Context.dev,
Octen, Parallel.ai, and Perplexity. The benchmark design and metric definitions
are documented in [the package README](web_search_benchmark_kit/README.md).

## Repository layout

- `web_search_benchmark_kit/runners/` contains retrieval and URL-health
  entry points.
- `web_search_benchmark_kit/analysis/` contains scoring, merging, and chart
  generation modules.
- `web_search_benchmark_kit/data/` contains the benchmark query catalog.
- `web_search_benchmark_kit/templates/` contains judging and run schemas.
- `web_search_benchmark_kit/docs/` contains the chart specification.
- `web_search_benchmark_kit/tests/` contains contract tests.
- `results/` contains a small, sanitized aggregate result release.
- `runs/` is intentionally local-only. It can contain raw requests, raw
  responses, provider identifiers, and other experiment artifacts.
- `scratch/` is local-only exploratory work.

Raw benchmark runs are not part of the repository. The curated result release
contains aggregate tables and selected charts without billing data, absolute
machine paths, private hostnames, request payloads, or raw provider responses.

## Quick start

Use Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r web_search_benchmark_kit/requirements.txt
```

Set the provider credentials for the services you want to test. The runner
reads these variables and never stores their values:

```bash
export EXA_API_KEY=...
export CONTEXT_DEV_API_KEY=...
export OCTEN_API_KEY=...
export PARALLEL_API_KEY=...
export PERPLEXITY_API_KEY=...
```

Kagi uses the installed subscriber-session `kagi` CLI path. It is not read
from a value committed to this repository.

Run retrieval into the ignored local `runs/` directory:

```bash
python web_search_benchmark_kit/runners/run_benchmark.py \
  --queries web_search_benchmark_kit/data/queries.csv \
  --out runs/local-run \
  --include-reference-queries
```

Generate operational charts from a local run:

```bash
python web_search_benchmark_kit/analysis/make_operational_charts.py \
  --run-dir runs/local-run \
  --out runs/local-run/charts
```

Run the benchmark tests:

```bash
python -m pytest web_search_benchmark_kit/tests/test_run_benchmark.py
```

## Data and interpretation

The benchmark query set and templates are included so the measurement design
can be inspected. Provider terms may restrict redistribution of captured
responses, snippets, or usage records, so raw API output stays outside the
repository. The published quality tables are aggregate diagnostics. The
metadata heuristic and blind AI-card view are not human landing-page judgments.

## License

The code is released under the MIT License. See [LICENSE](LICENSE).
