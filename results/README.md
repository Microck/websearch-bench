# Curated benchmark results

This directory is the sanitized publication slice of the combined retrieval
run from 2026-08-05. It contains aggregate tables and selected charts only.

The release covers 80 primary queries, 16 reference queries, six providers,
and 576 requests in one repetition.

## Files

- `rep-1-20260805/manifest.json` describes the published scope and redactions.
- `rep-1-20260805/request-summary.csv` contains aggregate latency, depth, and
  success counts.
- `rep-1-20260805/quality-summary.csv` contains provisional metadata-heuristic
  quality metrics.
- `rep-1-20260805/ai-quality-summary.csv` contains the blind AI-card quality
  view. It is a metadata proxy, not human ground truth.
- `rep-1-20260805/charts/` contains selected aggregate PNG charts.

## Deliberate exclusions

The publication slice does not include raw requests, raw responses, result
snippets, request IDs, provider billing or account data, absolute local paths,
private machine or network identifiers, AI judging packets, AI reasoning, or
network-probe evidence.

The full local runs remain under the ignored `runs/` directory for analysis.
They should be reviewed separately before being shared with anyone else.

## Interpretation

The quality tables use the benchmark's provisional metadata heuristic and blind
AI-card view. Neither measure is a human review of the linked landing pages.
The charts should be read with the method limitations in
`docs/benchmark-design.md`.
