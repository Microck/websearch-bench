Web-search benchmark visualization specification

Main presentation charts

1. Horizontal dot-and-whisker plot (forest-plot style)
   Data: one nDCG@10 value per provider per query.
   Encoding: dot = mean nDCG@10; whisker = paired-bootstrap 95% confidence interval.
   Message: overall quality and uncertainty.
   This should be the first quality chart. It is more honest than a plain bar chart.

2. Category performance heatmap
   Data: mean nDCG@10 by provider and category.
   Encoding: rows = providers; columns = query categories; cell = score.
   Message: where each provider is strong or weak.
   Keep nDCG@10 as the common heatmap metric. Put specialist metrics in a separate scorecard.

3. Win–tie–loss 100% stacked bar chart
   Data: paired per-query nDCG@10 differences for each provider pair.
   Encoding: win when delta >= 0.05, loss when delta <= -0.05, otherwise tie.
   Message: how often one provider materially beats another.
   Predeclare the 0.05 practical-tie threshold before looking at results.

4. Quality–latency–cost bubble scatter plot with Pareto frontier
   Data: overall mean nDCG@10, p95 latency and actual cost/query.
   Encoding: x = p95 latency; y = nDCG@10; bubble area = cost/query.
   Message: the quality/speed/cost tradeoff.
   Label every point and draw the non-dominated Pareto frontier.

5. Empirical cumulative distribution function (ECDF) plot
   Data: all successful request latencies.
   Encoding: x = latency in milliseconds, preferably log-scaled; y = fraction of requests completed.
   Message: typical and tail latency without hiding the distribution behind one average.

6. UpSet plot
   Data: relevant query–URL pairs, where relevance grade >= 2, and the providers returning each pair.
   Encoding: intersection size for each provider-membership combination.
   Message: which providers retrieve the same relevant material and which find unique useful pages.
   Use an UpSet plot, not a four-way Venn diagram. Overlap is descriptive, not a quality score.

Diagnostic and appendix charts

7. Box-and-whisker plot with jittered query points
   Data: per-query nDCG@10.
   Message: consistency, catastrophic misses and outliers.

8. Mean relevance-by-rank line chart
   Data: mean relevance grade or probability of grade >= 2 at ranks 1 through 10.
   Message: how quickly result quality decays down the page.

9. Paired-difference forest plot
   Data: per-query nDCG differences for all six provider pairs.
   Encoding: point = mean paired difference; whisker = 95% paired-bootstrap interval; vertical line = zero.
   Message: effect size and whether the apparent gap is credibly different from zero.

10. Cumulative time-to-detection step chart (Kaplan–Meier style)
    Data: repeated searches after a timestamped official publication.
    Encoding: x = hours since publication; y = percentage of events detected in the top 3.
    Message: indexing freshness.
    This requires a live-event freshness test, not only the rolling freshness queries.

11. Error-composition 100% stacked bar chart
    Data: timeout, 429, 5xx, invalid response and other failures.
    Message: reliability and the kind of failure users will experience.

Recommended one-page dashboard order

A. Overall quality dot-and-whisker
B. Category heatmap
C. Win–tie–loss chart
D. Quality–latency–cost Pareto plot
E. Latency ECDF
F. UpSet plot

Raw-study diagnostics

These diagnostics use raw request logs, retrieved result rows, URL resolution,
query categories, and billing estimates. They do not use relevance labels.

1. Provider-by-category latency heatmap
   Data: successful primary-request latency grouped by provider and category.
   Encoding: cell color = median milliseconds; text = median and p95.
   Message: which workload categories create latency hotspots.

2. Provider-by-category completeness heatmap
   Data: observed result-row count per primary request.
   Encoding: cell color = percent of requests with fewer than ten results; text = mean result count.
   Message: where a requested ten-result list is shorter.

3. Raw result-set agreement matrix
   Data: canonical URL sets from each provider's primary top ten.
   Encoding: cell = mean Jaccard overlap across the 80 query lists.
   Message: which providers return similar pages, and where their result sets diverge.

4. URL resolution health by rank
   Data: primary result rows joined to the URL resolver by canonical URL.
   Encoding: stacked direct, redirected, broken/error, and unresolved shares plus reachable share by rank.
   Message: how much of each result list is likely clickable.

5. Source ecology stacked bars
   Data: hostname share per provider and primary query list.
   Encoding: equal-weighted mean share for the eight most common hosts plus Other hosts.
   Message: where result slots come from, without implying source quality.

6. Cost per returned result slot
   Data: full-repetition billing basis divided by all returned result rows.
   Encoding: cost per 1,000 returned slots, with full-list percentage annotated.
   Message: cost after accounting for partial result lists. Keep reported and estimated billing modes explicit.

Avoid using a radar/spider chart as the main comparison. It makes area comparisons unreliable and cannot show uncertainty.
