# Codex single-provider run — 2026-08-08

Raw benchmark output from a Codex-only retrieval run (96 queries, 1 repetition).
Unlike the sanitized `rep-1-20260805/` slice, these files are unredacted run data:
`retrieved_results.csv` includes result URLs, titles, and snippets.

## Files

- `runs.csv` — per-query latency, HTTP status, and cost.
- `retrieved_results.csv` — ranked results (URL, canonical URL, title, snippet).

## Provenance

- Provider: `codex` (`codex exec --json`)
- Query catalog: `data/queries.csv`
- Harness config is not tracked here; see the local `runs/codex/run_manifest.json`.
