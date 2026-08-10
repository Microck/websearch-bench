# Automated judgment method

These labels are `auto_heuristic_v1`, not human gold judgments. The scorer uses only the query text, intent, target hints, subtopics, result URL, title/snippets, limited URL-resolution metadata, and page reachability. It does not use provider or rank when assigning labels.

Grade 3 means the heuristic found a strong direct match or all declared constraints. Grade 2 means a useful relevant match. Grade 1 means partial or weak topical evidence. Grade 0 means no match, spam, or an unreachable result. The resulting nDCG/MRR/Success charts are automated diagnostics and must not be described as human relevance scores.
