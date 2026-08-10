# Curated benchmark results

This directory is the sanitized publication slice of the combined retrieval
run from 2026-08-08. It contains aggregate tables, selected charts, and
the underlying per-query raw data so anyone can inspect individual results.

The release covers 80 primary queries, 16 reference queries, eight providers,
and 768 requests in one repetition.

## Files

### Aggregate summaries

- `request-summary.csv` — aggregate latency, depth, and success counts per
  provider.
- `quality-summary.csv` — provisional metadata-heuristic quality metrics per
  provider.
- `ai-quality-summary.csv` — blind AI-card quality view per provider. This is
  a metadata proxy, not human ground truth.
- `charts/` — selected aggregate PNG charts.

### Raw per-query data (`raw/`)

The `raw/` directory contains the full per-query, per-provider result data
with PII and billing identifiers removed. These files let anyone verify
scores by checking exactly which URLs each provider returned for each query.

- `raw/retrieved_results.csv` — every URL returned by every provider for every
  query, with rank, title, snippet, and canonical URL (~102K rows).
- `raw/runs.csv` — per-request timing: query ID, provider, latency, HTTP
  status, and error type (~768 rows). Provider request IDs, local file paths,
  and per-request costs are stripped.
- `raw/run_summary.csv` — per-provider aggregate latency and result depth.
  Per-request cost columns are stripped.
- `raw/run_manifest.json` — run metadata (absolute paths replaced with
  relative).
- `raw/pooled_judgments_primary.csv` — pooled auto-heuristic relevance
  judgments for primary queries (~8.9K URL assessments).
- `raw/pooled_judgments_reference.csv` — pooled auto-heuristic judgments for
  reference queries.
- `raw/pooled_judgments_primary_ai.csv` — pooled blind AI-card relevance
  judgments for primary queries.
- `raw/scored_results_primary.csv` — per-rank scored results (heuristic
  assessor) for primary queries (~14K rows).
- `raw/scored_results_reference.csv` — per-rank scored results for reference
  queries.
- `raw/scored_results_primary_ai.csv` — per-rank scored results (AI-card
  assessor) for primary queries.
- `raw/provider_cost_estimates.csv` — per-provider cost estimates derived from
  published rates and observed billing. No account-specific data.
- `raw/judgment-method.md` — auto-heuristic scoring method description.
- `raw/ai-judging-method.md` — AI-card judging method description.

### Manifest

- `manifest.json` — describes the published scope and redactions.

## Deliberate exclusions

The publication slice does not include raw JSON API responses (which contain
session IDs, search IDs, and provider-specific metadata), raw request
payloads, provider request identifiers, per-request cost or credit fields,
provider billing or account data, absolute local paths, private machine or
network identifiers, AI judging packets, AI reasoning traces, or
network-probe evidence (URL resolution data).

The full local runs remain under the ignored `runs/` directory for analysis.
They should be reviewed separately before being shared with anyone else.

## Interpretation

The quality tables use the benchmark's provisional metadata heuristic and
blind AI-card view. Neither measure is a human review of the linked landing
pages. The charts should be read with the method limitations in
`docs/benchmark-design.md`.
