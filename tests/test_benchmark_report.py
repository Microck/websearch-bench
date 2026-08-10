import unittest

import pandas as pd

from analysis.build_benchmark_report import build_category_metrics, build_html, build_query_outcomes, match_gold


class BenchmarkReportTests(unittest.TestCase):
    def test_report_embeds_accessible_svg_graphs_instead_of_chart_images(self) -> None:
        providers = [
            {
                "provider": "context",
                "label": "Context.dev",
                "color": "#5e6ad2",
                "queries": 1,
                "graded_queries": 1,
                "successful_attempts": 1,
                "failed_attempts": 0,
                "result_rows": 8,
                "mean_results": 8.0,
                "hit_at_1": 50.0,
                "hit_at_10": 100.0,
                "lift": 50.0,
            },
            {
                "provider": "exa",
                "label": "Exa",
                "color": "#1840ed",
                "queries": 1,
                "graded_queries": 1,
                "successful_attempts": 1,
                "failed_attempts": 0,
                "result_rows": 10,
                "mean_results": 10.0,
                "hit_at_1": 100.0,
                "hit_at_10": 100.0,
                "lift": 0.0,
            },
        ]
        page_data = {
            "run": {
                "name": "fixture-run",
                "created_at": "2026-08-09",
                "queries_attempted": 1,
                "graded_queries": 1,
                "ungraded_queries": 0,
                "ungraded_query_ids": [],
                "provider_count": 2,
                "direct_calls": 2,
                "failed_attempts": 0,
            },
            "providers": providers,
            "categories": [
                {
                    "provider": provider["provider"],
                    "category": "tech_code",
                    "category_label": "Tech and code",
                    "queries": 1,
                    "hit_at_1": provider["hit_at_1"],
                    "hit_at_10": provider["hit_at_10"],
                }
                for provider in providers
            ],
            "queries": [
                {
                    "qid": "q1",
                    "query": "official docs",
                    "category": "tech_code",
                    "category_label": "Tech and code",
                    "graded": True,
                    "providers": {
                        provider["provider"]: {
                            "graded": True,
                            "first_gold_rank": 1,
                            "hit_at_1": True,
                            "hit_at_10": True,
                            "result_count": provider["result_rows"],
                        }
                        for provider in providers
                    },
                }
            ],
            "usage": {"context_credits": 1.0, "exa_cost_usd": 0.01, "context_key_request_split": {}},
        }

        report = build_html(page_data)

        self.assertEqual(report.count('<svg class="plot '), 3)
        self.assertIn('role="img"', report)
        self.assertIn('aria-labelledby="hit-rate-title hit-rate-desc"', report)
        self.assertIn('class="plot-scroll"', report)
        self.assertNotIn(".png", report)
        self.assertNotIn("<img", report)

    def test_match_gold_normalizes_prefix_urls(self) -> None:
        anchor = {
            "gold": {
                "gold_urls": ["https://docs.example.test/"],
                "url_match": "prefix",
            }
        }
        self.assertTrue(match_gold("https://DOCS.example.test/guide/?utm_source=feed", anchor))
        self.assertFalse(match_gold("https://other.example.test/guide", anchor))

    def test_query_outcomes_preserve_first_matching_rank_and_ungraded_anchors(self) -> None:
        anchors = [
            {
                "qid": "q1",
                "query": "official docs",
                "vertical": "tech_code",
                "gold": {"gold_urls": ["https://docs.example.test/"], "url_match": "prefix"},
            },
            {"qid": "q2", "query": "ungraded query", "vertical": "general"},
        ]
        results = pd.DataFrame(
            [
                {"provider": "exa", "query_id": "q1", "rank": "1", "url": "https://other.test", "canonical_url": "https://other.test"},
                {"provider": "exa", "query_id": "q1", "rank": "3", "url": "https://docs.example.test/guide", "canonical_url": "https://docs.example.test/guide"},
                {"provider": "context", "query_id": "q1", "rank": "1", "url": "https://docs.example.test", "canonical_url": "https://docs.example.test"},
            ]
        )
        outcomes = build_query_outcomes(anchors, ["exa", "context"], results)
        self.assertEqual(outcomes[0]["providers"]["exa"]["first_gold_rank"], 3)
        self.assertFalse(outcomes[0]["providers"]["exa"]["hit_at_1"])
        self.assertTrue(outcomes[0]["providers"]["exa"]["hit_at_10"])
        self.assertTrue(outcomes[0]["providers"]["context"]["hit_at_1"])
        self.assertFalse(outcomes[1]["graded"])
        self.assertFalse(outcomes[1]["providers"]["exa"]["hit_at_10"])

    def test_category_metrics_use_only_graded_queries(self) -> None:
        outcomes = [
            {
                "category": "tech_code",
                "category_label": "Tech and code",
                "graded": True,
                "providers": {
                    "exa": {"hit_at_1": False, "hit_at_10": True},
                    "context": {"hit_at_1": True, "hit_at_10": True},
                },
            },
            {
                "category": "general",
                "category_label": "General",
                "graded": False,
                "providers": {
                    "exa": {"hit_at_1": False, "hit_at_10": False},
                    "context": {"hit_at_1": False, "hit_at_10": False},
                },
            },
        ]
        rows = build_category_metrics(outcomes, ["exa", "context"])
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["category"] for row in rows}, {"tech_code"})
        self.assertEqual(rows[0]["queries"], 1)


if __name__ == "__main__":
    unittest.main()
