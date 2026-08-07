Web Search API Benchmark Kit

Scope

Compare one raw search request returning one ranked list of ten webpages:

- Exa: Search API, type=auto, numResults=10, no contents or deep mode
- Kagi: subscriber-session search through the installed `kagi-cli`, first ten web results
- Context.dev: numResults=10, queryFanout=false, markdownOptions.enabled=false
- Octen: topic=general, count=10, no full_content and no images
- Parallel.ai: Search API, one query, `mode=basic`, first ten ranked results
- Perplexity: Search API, `max_results=10`, `search_context_size=low`

Do not use generated answers, query fanout, broad/deep search, page extraction, crawling or agent loops.

Files

- queries.csv / queries.jsonl: 80 primary benchmark queries in ten categories
- judgments_template.csv: blind pooled relevance-judgment schema
- runs_template.csv: request-level latency, cost and failure schema
- scored_results_template.csv: normalized ranked results joined to judgments
- chart_spec.md: exact graph names, required data and intended message
- plot_report.py: generates the core report charts from scored results and run logs
- make_operational_charts.py: generates latency, result-depth, cost and distribution charts without relevance scores
- make_study_charts.py: generates category, retrieval-agreement, URL-health, source-ecology and delivered-slot-cost diagnostics from raw study data

Run design

1. Send each identical query to every provider and request ten results.
2. Randomize provider order within each query.
3. Run the complete suite three times on separate days.
4. Save the untouched API request and response.
5. Resolve redirects and canonicalize URLs.
6. Pool the union of all top-ten results for each query.
7. Deduplicate by canonical URL and hide provider/rank before judging.
8. Judge the actual landing page on a 0-3 scale.

Relevance scale

0 = irrelevant, wrong intent, broken, spam or misleading
1 = tangential or materially incomplete
2 = relevant and useful
3 = directly satisfies the stated intent

Category-specific measurement

known_item:
  MRR@10, Success@1 and nDCG@10

primary_source:
  nDCG@10, Official@1 and Official@3

technical_exact:
  nDCG@10, MRR@10 and Success@3

semantic_long_tail:
  nDCG@10, Success@3 and canonical-concept match

niche_small_web:
  nDCG@10, Independent-source@5 and Success@3

multi_constraint:
  Constraint-Success@3, constraint coverage@5 and nDCG@10
  Mark every mandatory constraint as met, not met or unknown.

exploratory_diversity:
  alpha-nDCG@10, subtopic recall@10 and ordinary nDCG@10
  Apply one or more predefined subtopic labels to every relevant page.

multilingual_regional:
  nDCG@10, correct-language@5 and local-official@3
  Optional: run the English reference query to measure cross-language parity.

robustness_noisy:
  Run both query and reference_query.
  Robustness loss = clean nDCG@10 minus noisy nDCG@10.
  Also report first-relevant-result rank displacement.

freshness_rolling:
  Before every run, identify the newest eligible official item and timestamp it.
  Report its rank, Success@3 and the staleness gap.

Universal metrics

- nDCG@10: primary relevance/ranking metric
- P@5: grade 2 or 3 counts as relevant
- Success@1 / Success@3: a grade-3 result appears within the cutoff
- p50 / p95 latency
- request error rate
- actual cost/query
- RBO@10 between repeated rankings for stability

Statistical reporting

- Use paired bootstrap confidence intervals over queries.
- Compare providers on paired per-query scores, never independent averages.
- Publish category-level results and the query-level data.
- Do not compress quality, latency and cost into one hidden weighted score.

Raw runner

`run_benchmark.py` executes the retrieval phase and writes request payloads,
untouched response bodies, normalized ranked results, request logs, and
provider-reported usage. It uses only the standard Python library.

```bash
EXA_API_KEY=... \
CONTEXT_DEV_API_KEY=... \
OCTEN_API_KEY=... \
PARALLEL_API_KEY=... \
PERPLEXITY_API_KEY=... \
python run_benchmark.py \
  --queries queries.csv \
  --out ../runs/rep-1 \
  --include-reference-queries
```

The runner supports `exa`, `context`, `octen`, `kagi_session`, `parallel`, and
`perplexity`. Parallel uses both `objective` and a one-item `search_queries`
array, with `mode=basic` to keep the request comparable to a direct ranked
search. Perplexity uses the ranked Search API, not a Sonar answer request. The Kagi
provider invokes the installed `kagi` CLI subscriber-session path. Keep it
separate from API-mode Kagi results because the two paths are not guaranteed to
share the same ranking behavior. The runner normalizes URL syntax and removes
obvious tracking parameters; it does not resolve redirects or assign
relevance grades. Use the pooled judgment templates after retrieval. The
`provider_usage.csv` output preserves Context credits, Octen search-query and
full-content usage, and Exa-reported cost without guessing prices for plans or
overage tiers. Parallel and Perplexity usage and cost remain in their raw JSON
responses unless the provider returns fields already represented by the usage
schema. Add explicit rows to `provider_billing_estimates.csv` before plotting
working cost estimates for either provider.

Generate the operational charts after a run:

```bash
python make_operational_charts.py \
  --run-dir ../runs/rep-1 \
  --out ../runs/rep-1/charts
```

Generate the raw-study diagnostics:

```bash
python make_study_charts.py \
  --run-dir ../runs/rep-1 \
  --out ../runs/rep-1/quality/study-charts
```

The quality charts from `plot_report.py` require completed blind judgments and
a populated scored-results file.
