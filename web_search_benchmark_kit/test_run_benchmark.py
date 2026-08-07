import os
import unittest

try:
    from .run_benchmark import DEFAULT_PROVIDERS, parse_args, provider_request, provider_results, result_fields
except ImportError:
    from run_benchmark import DEFAULT_PROVIDERS, parse_args, provider_request, provider_results, result_fields


class ProviderContractTests(unittest.TestCase):
    def test_default_provider_set_includes_new_search_apis(self) -> None:
        args = parse_args(["--out", "/tmp/provider-contract-test"])
        self.assertIn("parallel", DEFAULT_PROVIDERS)
        self.assertIn("perplexity", DEFAULT_PROVIDERS)
        self.assertIn("parallel", args.providers)
        self.assertIn("perplexity", args.providers)

    def setUp(self) -> None:
        self.previous_parallel = os.environ.get("PARALLEL_API_KEY")
        self.previous_perplexity = os.environ.get("PERPLEXITY_API_KEY")
        os.environ["PARALLEL_API_KEY"] = "test-parallel-key"
        os.environ["PERPLEXITY_API_KEY"] = "test-perplexity-key"

    def tearDown(self) -> None:
        if self.previous_parallel is None:
            os.environ.pop("PARALLEL_API_KEY", None)
        else:
            os.environ["PARALLEL_API_KEY"] = self.previous_parallel
        if self.previous_perplexity is None:
            os.environ.pop("PERPLEXITY_API_KEY", None)
        else:
            os.environ["PERPLEXITY_API_KEY"] = self.previous_perplexity

    def test_parallel_request_shape(self) -> None:
        url, headers, payload = provider_request("parallel", "find the official Python docs")
        self.assertEqual(url, "https://api.parallel.ai/v1/search")
        self.assertEqual(headers, {"x-api-key": "test-parallel-key"})
        self.assertEqual(payload["search_queries"], ["find the official Python docs"])
        self.assertEqual(payload["objective"], "find the official Python docs")
        self.assertEqual(payload["mode"], "basic")

    def test_perplexity_request_shape(self) -> None:
        url, headers, payload = provider_request("perplexity", "find the official Python docs")
        self.assertEqual(url, "https://api.perplexity.ai/search")
        self.assertEqual(headers, {"Authorization": "Bearer test-perplexity-key"})
        self.assertEqual(
            payload,
            {
                "query": "find the official Python docs",
                "max_results": 10,
                "search_context_size": "low",
            },
        )

    def test_parallel_result_normalization(self) -> None:
        results, request_id, cost = provider_results(
            "parallel",
            {"search_id": "parallel-search-1", "results": [{"url": "https://example.com"}]},
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(request_id, "parallel-search-1")
        self.assertEqual(cost, "")
        self.assertEqual(
            result_fields(
                "parallel",
                {
                    "url": "https://example.com/",
                    "title": "Example",
                    "excerpts": ["First excerpt", "Second excerpt"],
                    "publish_date": "2026-08-05",
                },
            ),
            ("https://example.com/", "https://example.com/", "Example", "First excerpt Second excerpt\t2026-08-05"),
        )

    def test_perplexity_result_normalization(self) -> None:
        results, request_id, cost = provider_results(
            "perplexity",
            {"id": "perplexity-search-1", "results": [{"url": "https://example.org"}]},
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(request_id, "perplexity-search-1")
        self.assertEqual(cost, "")
        self.assertEqual(
            result_fields(
                "perplexity",
                {
                    "url": "https://example.org/page",
                    "title": "Example page",
                    "snippet": "A useful result",
                    "date": "2026-08-04",
                },
            ),
            ("https://example.org/page", "https://example.org/page", "Example page", "A useful result\t2026-08-04"),
        )


if __name__ == "__main__":
    unittest.main()
