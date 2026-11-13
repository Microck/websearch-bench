import os
import unittest

from runners.run_benchmark import (
    DEFAULT_PROVIDERS,
    SUPPORTED_PROVIDERS,
    parse_args,
    provider_request,
    provider_results,
    result_fields,
)


class ProviderContractTests(unittest.TestCase):
    def test_default_provider_set_includes_new_search_apis(self) -> None:
        args = parse_args(["--out", "/tmp/provider-contract-test"])
        self.assertIn("parallel", DEFAULT_PROVIDERS)
        self.assertIn("perplexity", DEFAULT_PROVIDERS)
        self.assertIn("brave", DEFAULT_PROVIDERS)
        self.assertIn("tavily", DEFAULT_PROVIDERS)
        self.assertIn("parallel", args.providers)
        self.assertIn("perplexity", args.providers)
        self.assertIn("brave", args.providers)
        self.assertIn("tavily", args.providers)

    def setUp(self) -> None:
        self.previous_context = os.environ.get("CONTEXT_DEV_API_KEY")
        self.previous_context_2 = os.environ.get("CONTEXT_DEV_API_KEY_2")
        self.previous_parallel = os.environ.get("PARALLEL_API_KEY")
        self.previous_perplexity = os.environ.get("PERPLEXITY_API_KEY")
        self.previous_brave = os.environ.get("BRAVE_API_KEY")
        self.previous_tavily = os.environ.get("TAVILY_API_KEY")
        os.environ["CONTEXT_DEV_API_KEY"] = "test-context-key"
        os.environ["CONTEXT_DEV_API_KEY_2"] = "test-context-key-2"
        os.environ["PARALLEL_API_KEY"] = "test-parallel-key"
        os.environ["PERPLEXITY_API_KEY"] = "test-perplexity-key"
        os.environ["BRAVE_API_KEY"] = "test-brave-key"
        os.environ["TAVILY_API_KEY"] = "test-tavily-key"

    def tearDown(self) -> None:
        if self.previous_context is None:
            os.environ.pop("CONTEXT_DEV_API_KEY", None)
        else:
            os.environ["CONTEXT_DEV_API_KEY"] = self.previous_context
        if self.previous_context_2 is None:
            os.environ.pop("CONTEXT_DEV_API_KEY_2", None)
        else:
            os.environ["CONTEXT_DEV_API_KEY_2"] = self.previous_context_2
        if self.previous_parallel is None:
            os.environ.pop("PARALLEL_API_KEY", None)
        else:
            os.environ["PARALLEL_API_KEY"] = self.previous_parallel
        if self.previous_perplexity is None:
            os.environ.pop("PERPLEXITY_API_KEY", None)
        else:
            os.environ["PERPLEXITY_API_KEY"] = self.previous_perplexity
        if self.previous_brave is None:
            os.environ.pop("BRAVE_API_KEY", None)
        else:
            os.environ["BRAVE_API_KEY"] = self.previous_brave
        if self.previous_tavily is None:
            os.environ.pop("TAVILY_API_KEY", None)
        else:
            os.environ["TAVILY_API_KEY"] = self.previous_tavily

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

    def test_brave_request_shape(self) -> None:
        url, headers, payload = provider_request("brave", "find the official Python docs")
        self.assertTrue(url.startswith("https://api.search.brave.com/res/v1/web/search?"))
        self.assertEqual(headers, {"X-Subscription-Token": "test-brave-key"})
        self.assertEqual(payload, {"query": "find the official Python docs", "count": 10})

    def test_tavily_request_shape(self) -> None:
        url, headers, payload = provider_request("tavily", "find the official Python docs")
        self.assertEqual(url, "https://api.tavily.com/search")
        self.assertEqual(headers, {"Authorization": "Bearer test-tavily-key"})
        self.assertEqual(payload, {"query": "find the official Python docs", "max_results": 10})

    def test_context_request_rotates_configured_keys(self) -> None:
        url, first_headers, first_payload = provider_request(
            "context",
            "find the official Python docs",
            context_api_key_index=0,
        )
        _, second_headers, _ = provider_request(
            "context",
            "find the official Python docs",
            context_api_key_index=1,
        )
        _, third_headers, _ = provider_request(
            "context",
            "find the official Python docs",
            context_api_key_index=2,
        )
        self.assertEqual(url, "https://api.context.dev/v1/web/search")
        self.assertEqual(first_headers, {"Authorization": "Bearer test-context-key"})
        self.assertEqual(second_headers, {"Authorization": "Bearer test-context-key-2"})
        self.assertEqual(third_headers, {"Authorization": "Bearer test-context-key"})
        self.assertEqual(
            first_payload,
            {
                "query": "find the official Python docs",
                "numResults": 10,
                "queryFanout": False,
                "markdownOptions": {"enabled": False},
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

    def test_brave_result_normalization(self) -> None:
        results, request_id, cost = provider_results(
            "brave",
            {"web": {"results": [{"url": "https://brave.example.org"}]}},
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(request_id, "")
        self.assertEqual(cost, "")
        self.assertEqual(
            result_fields(
                "brave",
                {
                    "url": "https://brave.example.org/page",
                    "title": "Brave result",
                    "description": "Brave description",
                    "page_age": "2026-08-01",
                },
            ),
            ("https://brave.example.org/page", "https://brave.example.org/page", "Brave result", "Brave description\t2026-08-01"),
        )

    def test_tavily_result_normalization(self) -> None:
        results, request_id, cost = provider_results(
            "tavily",
            {"request_id": "tavily-req-1", "results": [{"url": "https://tavily.example.org"}]},
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(request_id, "tavily-req-1")
        self.assertEqual(cost, "")
        self.assertEqual(
            result_fields(
                "tavily",
                {
                    "url": "https://tavily.example.org/page",
                    "title": "Tavily result",
                    "content": "Tavily content",
                    "published_date": "2026-08-02",
                },
            ),
            ("https://tavily.example.org/page", "https://tavily.example.org/page", "Tavily result", "Tavily content\t2026-08-02"),
        )

    def test_keenable_is_opt_in_not_default(self) -> None:
        args = parse_args(["--out", "/tmp/provider-contract-test"])
        self.assertIn("keenable", SUPPORTED_PROVIDERS)
        self.assertNotIn("keenable", DEFAULT_PROVIDERS)
        self.assertNotIn("keenable", args.providers)
        selected = parse_args(["--out", "/tmp/provider-contract-test", "--providers", "keenable"])
        self.assertEqual(selected.providers, ["keenable"])

    def test_keenable_request_shape_keyed(self) -> None:
        previous = os.environ.get("KEENABLE_API_KEY")
        os.environ["KEENABLE_API_KEY"] = "test-keenable-key"
        try:
            url, headers, payload = provider_request("keenable", "find the official Python docs")
        finally:
            if previous is None:
                os.environ.pop("KEENABLE_API_KEY", None)
            else:
                os.environ["KEENABLE_API_KEY"] = previous
        self.assertEqual(url, "https://api.keenable.ai/v1/search")
        self.assertEqual(headers, {"X-API-Key": "test-keenable-key", "X-Keenable-Title": "websearch-bench"})
        self.assertEqual(payload, {"query": "find the official Python docs", "max_results": 10})

    def test_keenable_request_shape_public(self) -> None:
        previous = os.environ.get("KEENABLE_API_KEY")
        os.environ.pop("KEENABLE_API_KEY", None)
        try:
            url, headers, payload = provider_request("keenable", "find the official Python docs")
        finally:
            if previous is not None:
                os.environ["KEENABLE_API_KEY"] = previous
        self.assertEqual(url, "https://api.keenable.ai/v1/search/public")
        self.assertEqual(headers, {"X-Keenable-Title": "websearch-bench"})
        self.assertEqual(payload, {"query": "find the official Python docs", "max_results": 10})

    def test_keenable_result_normalization(self) -> None:
        results, request_id, cost = provider_results(
            "keenable",
            {"query": "q", "mode": "pro", "results": [{"url": "https://keenable.example.org"}]},
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(request_id, "")
        self.assertEqual(cost, "")
        self.assertEqual(
            result_fields(
                "keenable",
                {
                    "url": "https://keenable.example.org/page",
                    "title": "Keenable result",
                    "snippet": "Keenable snippet",
                    "published_at": "2026-08-03T00:00:00Z",
                },
            ),
            ("https://keenable.example.org/page", "https://keenable.example.org/page", "Keenable result", "Keenable snippet\t2026-08-03T00:00:00Z"),
        )


if __name__ == "__main__":
    unittest.main()
