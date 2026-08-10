# AI judging method

This is a second quality view. It does not replace the metadata heuristic or
claim human ground truth.

- Scope: 80 primary queries and 3,510 unique pooled result cards.
- The six-provider card set exactly matched the prior combined run, so its
  3,181 blind grades were reused.
- Five new blind batches judged the 329 cards added by the Brave and Tavily
  results.
- Each card showed only the query, title, snippets, page title, URL, and an
  anonymous result ID. Provider, original rank, and category were hidden.
- No judge browsed or searched the web.
- Grade 3 means directly satisfies the query. Grade 2 means relevant and
  useful. Grade 1 means partial or weak. Grade 0 means irrelevant, wrong
  intent, spam, misleading, or clearly broken.
- A repeated canonical URL received one judgment and that grade was joined
  back to every provider and rank occurrence after judging.
- Batches did not overlap, so inter-agent agreement was not measured.
- The exported `reason` field is a short evidence summary, not private chain
  of thought.
- nDCG@10 uses the same ranking calculation as the heuristic charts. For each
  query, the highest grade for each canonical URL is kept, and those grades
  are sorted from high to low to form the pooled reference list.

This measures relevance of stored result-card metadata. It is not a full
landing-page review, and it is not independent human assessment.
