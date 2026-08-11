#!/usr/bin/env python3
"""Run the raw top-ten web-search benchmark requests.

The runner intentionally stops at retrieval. It does not generate answers,
fetch result pages, or assign relevance grades. Those steps belong to the
blind pooled-judgment phase described in README.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from http.client import HTTPMessage
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


RUN_FIELDS = [
    "query_id",
    "query_variant",
    "provider",
    "repetition",
    "started_at",
    "latency_ms",
    "http_status",
    "error_type",
    "cost_usd",
    "request_id",
    "raw_response_path",
]

RESULT_FIELDS = [
    "query_id",
    "query_variant",
    "category",
    "provider",
    "repetition",
    "rank",
    "url",
    "canonical_url",
    "title",
    "snippet",
    "published_at",
    "raw_response_path",
]

USAGE_FIELDS = [
    "query_id",
    "query_variant",
    "provider",
    "repetition",
    "credits_consumed",
    "num_search_queries",
    "full_content_tokens",
    "cost_usd_reported",
    "raw_response_path",
]

DEFAULT_PROVIDERS = ("exa", "context", "octen", "kagi_session", "parallel", "perplexity", "brave", "tavily")
CONTEXT_API_KEY_NAMES = ("CONTEXT_DEV_API_KEY", "CONTEXT_DEV_API_KEY_2")
DEFAULT_QUERY_FILE = Path(__file__).resolve().parents[1] / "data" / "queries.csv"
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_",
    "referrer",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def compact_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def canonicalize_url(value: str) -> str:
    """Normalize obvious tracking noise without pretending to resolve redirects."""
    parsed = urlsplit(value.strip())
    if not parsed.scheme or not parsed.netloc:
        return value.strip()

    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    port = parsed.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        hostname = f"{hostname}:{port}"
    netloc = hostname
    if parsed.username or parsed.password:
        # Search result URLs should not normally contain credentials. Preserve
        # the host only so credentials never leak into the evaluation files.
        netloc = hostname

    query = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        lower_key = key.lower()
        if lower_key.startswith("utm_") or lower_key in TRACKING_QUERY_KEYS:
            continue
        query.append((key, item))

    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, urlencode(query, doseq=True), ""))


def classify_error(status: int, detail: str = "") -> str:
    lowered = detail.lower()
    if "timeout" in lowered or status == 408:
        return "timeout"
    if status == 429 or "rate limit" in lowered or "usage_limt" in lowered:
        return "rate_limit"
    if 400 <= status < 500:
        return "http_4xx"
    if 500 <= status < 600:
        return "http_5xx"
    if detail:
        return detail
    return ""


def parse_json(body: bytes) -> Any:
    return json.loads(body.decode("utf-8"))


def get_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return ""


def get_context_api_keys() -> list[str]:
    keys: list[str] = []
    for name in CONTEXT_API_KEY_NAMES:
        value = os.environ.get(name)
        if value:
            keys.append(value)
    return keys


def provider_request(
    provider: str,
    query: str,
    *,
    context_api_key_index: int = 0,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    if provider == "exa":
        key = get_env("EXA_API_KEY", "EXA_API_TOKEN")
        if not key:
            raise RuntimeError("missing EXA_API_KEY or EXA_API_TOKEN")
        return (
            "https://api.exa.ai/search",
            {"x-api-key": key},
            {"query": query, "type": "auto", "numResults": 10},
        )

    if provider == "context":
        keys = get_context_api_keys()
        if not keys:
            raise RuntimeError("missing CONTEXT_DEV_API_KEY or CONTEXT_DEV_API_KEY_2")
        key = keys[context_api_key_index % len(keys)]
        return (
            "https://api.context.dev/v1/web/search",
            {"Authorization": f"Bearer {key}"},
            {
                "query": query,
                "numResults": 10,
                "queryFanout": False,
                "markdownOptions": {"enabled": False},
            },
        )

    if provider == "octen":
        key = get_env("OCTEN_API_KEY", "OCTEN_API_TOKEN")
        if not key:
            raise RuntimeError("missing OCTEN_API_KEY or OCTEN_API_TOKEN")
        return (
            "https://api.octen.ai/search",
            {"x-api-key": key},
            {
                "query": query,
                "topic": "general",
                "count": 10,
                "full_content": {"enable": False},
                "include_images": False,
            },
        )

    if provider == "parallel":
        key = get_env("PARALLEL_API_KEY")
        if not key:
            raise RuntimeError("missing PARALLEL_API_KEY")
        return (
            "https://api.parallel.ai/v1/search",
            {"x-api-key": key},
            {
                # Supplying both fields keeps this a single-query benchmark
                # while satisfying the API's objective/query request shape.
                "objective": query,
                "search_queries": [query],
                "mode": "basic",
            },
        )

    if provider == "perplexity":
        key = get_env("PERPLEXITY_API_KEY", "PPLX_API_KEY")
        if not key:
            raise RuntimeError("missing PERPLEXITY_API_KEY or PPLX_API_KEY")
        return (
            "https://api.perplexity.ai/search",
            {"Authorization": f"Bearer {key}"},
            {
                "query": query,
                "max_results": 10,
                "search_context_size": "low",
            },
        )

    if provider == "brave":
        key = get_env("BRAVE_API_KEY", "BRAVE_SEARCH_API_KEY")
        if not key:
            raise RuntimeError("missing BRAVE_API_KEY or BRAVE_SEARCH_API_KEY")
        return (
            "https://api.search.brave.com/res/v1/web/search?" + urlencode({"q": query, "count": 10}),
            {"X-Subscription-Token": key},
            {"query": query, "count": 10},
        )

    if provider == "tavily":
        key = get_env("TAVILY_API_KEY", "TVLY_API_KEY")
        if not key:
            raise RuntimeError("missing TAVILY_API_KEY or TVLY_API_KEY")
        return (
            "https://api.tavily.com/search",
            {"Authorization": f"Bearer {key}"},
            {"query": query, "max_results": 10},
        )

    raise ValueError(f"provider {provider!r} is not an HTTP provider")


def request_json(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: float,
    method: str = "POST",
) -> tuple[int, float, bytes, HTTPMessage | None, str]:
    data = compact_json(payload) if method.upper() == "POST" else None
    request = Request(
        url,
        data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "web-search-benchmark-kit/1.0",
            **headers,
        },
        method=method,
    )
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read()
            status = int(response.status)
            response_headers = response.headers
            detail = ""
    except HTTPError as error:
        body = error.read()
        status = int(error.code)
        response_headers = error.headers
        detail = body.decode("utf-8", errors="replace")[:500]
    except (TimeoutError, URLError, OSError) as error:
        body = str(error).encode("utf-8", errors="replace")
        status = 0
        response_headers = None
        detail = str(error)
    elapsed_ms = (time.perf_counter() - started) * 1000
    return status, elapsed_ms, body, response_headers, detail


def run_kagi_session(query: str, timeout_seconds: float) -> tuple[int, float, bytes, bytes, str]:
    command = ["kagi", "search", "--limit", "10", "--format", "json", query]
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
        status = 200 if completed.returncode == 0 else 1
        stderr = completed.stderr
        detail = stderr.decode("utf-8", errors="replace")[:1000]
        match = re.search(r"HTTP (\d{3})", detail)
        if match:
            status = int(match.group(1))
        return status, (time.perf_counter() - started) * 1000, completed.stdout, stderr, detail
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout or b""
        stderr = error.stderr or b""
        return 408, (time.perf_counter() - started) * 1000, stdout, stderr, "timeout"


def provider_results(provider: str, payload: Any) -> tuple[list[dict[str, Any]], str, str]:
    if provider == "exa":
        results = payload.get("results") if isinstance(payload, dict) else None
        request_id = str(payload.get("requestId", "")) if isinstance(payload, dict) else ""
        total_cost = ""
        if isinstance(payload, dict):
            cost = payload.get("costDollars")
            if isinstance(cost, dict) and cost.get("total") is not None:
                total_cost = str(cost["total"])
    elif provider == "context":
        results = payload.get("results") if isinstance(payload, dict) else None
        request_id = ""
        total_cost = ""
    elif provider == "octen":
        data = payload.get("data") if isinstance(payload, dict) else None
        results = data.get("results") if isinstance(data, dict) else None
        request_id = str(payload.get("request_id", "")) if isinstance(payload, dict) else ""
        total_cost = ""
    elif provider == "kagi_session":
        results = payload.get("data") if isinstance(payload, dict) else None
        request_id = ""
        total_cost = ""
    elif provider == "parallel":
        results = payload.get("results") if isinstance(payload, dict) else None
        request_id = str(payload.get("search_id", "")) if isinstance(payload, dict) else ""
        total_cost = ""
    elif provider == "perplexity":
        results = payload.get("results") if isinstance(payload, dict) else None
        request_id = str(payload.get("id", "")) if isinstance(payload, dict) else ""
        total_cost = ""
    elif provider == "brave":
        web = payload.get("web") if isinstance(payload, dict) else None
        results = web.get("results") if isinstance(web, dict) else None
        request_id = ""
        total_cost = ""
    elif provider == "tavily":
        results = payload.get("results") if isinstance(payload, dict) else None
        request_id = str(payload.get("request_id", "")) if isinstance(payload, dict) else ""
        total_cost = ""
    else:
        raise ValueError(provider)

    if not isinstance(results, list):
        raise ValueError("invalid_response: result list missing")
    return results, request_id, total_cost


def provider_usage(provider: str, payload: Any) -> dict[str, Any]:
    """Extract provider-reported usage without inventing dollar costs."""
    if not isinstance(payload, dict):
        return {}
    if provider == "context":
        metadata = payload.get("key_metadata")
        return metadata if isinstance(metadata, dict) else {}
    if provider == "octen":
        metadata = payload.get("meta")
        usage = metadata.get("usage") if isinstance(metadata, dict) else None
        return usage if isinstance(usage, dict) else {}
    if provider == "exa":
        cost = payload.get("costDollars")
        if isinstance(cost, dict):
            return {"cost_usd_reported": cost.get("total", "")}
    return {}


def result_fields(provider: str, item: dict[str, Any]) -> tuple[str, str, str, str]:
    url = str(item.get("url") or "")
    title = str(item.get("title") or "")
    if provider == "exa":
        snippet = str(item.get("text") or item.get("summary") or "")
        published = str(item.get("publishedDate") or "")
    elif provider == "context":
        snippet = str(item.get("description") or "")
        published = ""
    elif provider == "octen":
        snippet = str(item.get("highlight") or "")
        published = str(item.get("time_published") or "")
    elif provider == "parallel":
        excerpts = item.get("excerpts")
        if isinstance(excerpts, list):
            snippet = " ".join(str(value).strip() for value in excerpts if str(value).strip())
        else:
            snippet = str(excerpts or "")
        published = str(item.get("publish_date") or "")
    elif provider == "perplexity":
        snippet = str(item.get("snippet") or "")
        published = str(item.get("date") or item.get("last_updated") or "")
    elif provider == "brave":
        snippet = str(item.get("description") or "")
        published = str(item.get("page_age") or item.get("age") or "")
    elif provider == "tavily":
        snippet = str(item.get("content") or item.get("snippet") or "")
        published = str(item.get("published_date") or "")
    else:
        snippet = str(item.get("snippet") or "")
        published = str(item.get("published") or "")
    return url, canonicalize_url(url), title, snippet + "\t" + published


def write_manifest(out_dir: Path, args: argparse.Namespace, query_count: int) -> None:
    manifest = {
        "created_at": utc_now(),
        "query_file": str(Path(args.queries).resolve()),
        "query_count": query_count,
        "repetitions": args.repetitions,
        "include_reference_queries": args.include_reference_queries,
        "providers": args.providers,
        "random_seed": args.seed,
        "request_timeout_seconds": args.timeout,
        "redirect_resolution": "not_performed; canonical URL normalization only",
        "provider_modes": {
            "context": "Context.dev Web Search API with 10 results; configured context keys are used round-robin",
            "parallel": "Parallel Search API mode=basic with one search_queries item",
            "perplexity": "Perplexity ranked Search API with max_results=10 and search_context_size=low",
            "brave": "Brave Search API Web Search endpoint with count=10",
            "tavily": "Tavily Search API endpoint with max_results=10",
        },
        "kagi_session_note": "kagi_session uses the subscriber CLI path, not the exhausted Kagi Search API key path",
        "usage_log": "provider_usage.csv",
    }
    (out_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def load_queries(path: Path, include_reference_queries: bool) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows.append({"query_id": row["query_id"], "category": row.get("category", ""), "query_variant": "primary", "query": row["query"]})
            reference = (row.get("reference_query") or "").strip()
            if include_reference_queries and reference:
                rows.append({"query_id": row["query_id"], "category": row.get("category", ""), "query_variant": "reference", "query": reference})
    return rows


def append_csv(path: Path, fields: list[str], row: dict[str, Any]) -> None:
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in fields})


def run(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "raw").mkdir(exist_ok=True)
    (out_dir / "requests").mkdir(exist_ok=True)
    queries = load_queries(Path(args.queries), args.include_reference_queries)
    if args.max_queries:
        query_ids = []
        for row in queries:
            if row["query_id"] not in query_ids:
                query_ids.append(row["query_id"])
        allowed = set(query_ids[: args.max_queries])
        queries = [row for row in queries if row["query_id"] in allowed]
    write_manifest(out_dir, args, len(queries))

    run_path = out_dir / "runs.csv"
    result_path = out_dir / "retrieved_results.csv"
    usage_path = out_dir / "provider_usage.csv"
    rng = random.Random(args.seed)
    total_requests = len(queries) * args.repetitions * len(args.providers)
    completed_requests = 0
    context_request_index = 0

    for repetition in range(1, args.repetitions + 1):
        for query_index, query_row in enumerate(queries, start=1):
            order = list(args.providers)
            rng.shuffle(order)
            query_statuses: list[str] = []
            for provider in order:
                completed_requests += 1
                started_at = utc_now()
                raw_dir = out_dir / "raw" / provider / f"rep_{repetition}"
                request_dir = out_dir / "requests" / provider / f"rep_{repetition}"
                raw_dir.mkdir(parents=True, exist_ok=True)
                request_dir.mkdir(parents=True, exist_ok=True)
                stem = f"{query_row['query_id']}__{query_row['query_variant']}"
                raw_path = raw_dir / f"{stem}.json"
                request_path = request_dir / f"{stem}.json"
                status = 0
                latency_ms = 0.0
                body = b""
                detail = ""
                request_id = ""
                cost_usd = ""
                reported_usage: dict[str, Any] = {}
                results: list[dict[str, Any]] = []
                error_type = ""

                try:
                    if provider == "kagi_session":
                        request_path.write_text(
                            json.dumps(
                                {"command": ["kagi", "search", "--limit", "10", "--format", "json", "<query>"], "query": query_row["query"]},
                                indent=2,
                            )
                            + "\n",
                            encoding="utf-8",
                        )
                        status, latency_ms, body, stderr, detail = run_kagi_session(query_row["query"], args.timeout)
                        raw_path.write_bytes(body)
                        if status == 200:
                            try:
                                payload = parse_json(body)
                                reported_usage = provider_usage(provider, payload)
                                results, request_id, cost_usd = provider_results(provider, payload)
                            except (ValueError, json.JSONDecodeError) as error:
                                error_type = str(error)
                        else:
                            error_type = classify_error(status, detail)
                            (raw_path.with_suffix(".stderr")).write_bytes(stderr)
                    else:
                        method = "GET" if provider in ("brave",) else "POST"
                        context_key_index = 0
                        if provider == "context":
                            context_key_index = context_request_index
                            context_request_index += 1
                        url, headers, payload = provider_request(
                            provider,
                            query_row["query"],
                            context_api_key_index=context_key_index,
                        )
                        request_path.write_bytes(compact_json({"method": method, "url": url, "payload": payload}) + b"\n")
                        status, latency_ms, body, response_headers, detail = request_json(url, headers, payload, args.timeout, method=method)
                        raw_path.write_bytes(body)
                        if 200 <= status < 300:
                            try:
                                payload = parse_json(body)
                                reported_usage = provider_usage(provider, payload)
                                results, request_id, cost_usd = provider_results(provider, payload)
                            except (ValueError, json.JSONDecodeError) as error:
                                error_type = str(error)
                        else:
                            error_type = classify_error(status, detail)
                except Exception as error:  # keep the suite moving and record the failure
                    detail = str(error)
                    error_type = classify_error(status, detail) or "runner_error"
                    if not raw_path.exists():
                        raw_path.write_bytes(detail.encode("utf-8", errors="replace"))

                if not error_type and not results:
                    error_type = "no_results"

                for rank, item in enumerate(results[:10], start=1):
                    if not isinstance(item, dict):
                        continue
                    url, canonical_url, title, snippet_and_date = result_fields(provider, item)
                    snippet, published_at = (snippet_and_date.split("\t", 1) + [""])[:2]
                    if not url:
                        continue
                    append_csv(
                        result_path,
                        RESULT_FIELDS,
                        {
                            "query_id": query_row["query_id"],
                            "query_variant": query_row["query_variant"],
                            "category": query_row["category"],
                            "provider": provider,
                            "repetition": repetition,
                            "rank": rank,
                            "url": url,
                            "canonical_url": canonical_url,
                            "title": title,
                            "snippet": snippet,
                            "published_at": published_at,
                            "raw_response_path": str(raw_path.relative_to(out_dir)),
                        },
                    )

                append_csv(
                    run_path,
                    RUN_FIELDS,
                    {
                        "query_id": query_row["query_id"],
                        "query_variant": query_row["query_variant"],
                        "provider": provider,
                        "repetition": repetition,
                        "started_at": started_at,
                        "latency_ms": f"{latency_ms:.3f}",
                        "http_status": status,
                        "error_type": error_type,
                        "cost_usd": cost_usd,
                        "request_id": request_id,
                        "raw_response_path": str(raw_path.relative_to(out_dir)),
                    },
                )
                append_csv(
                    usage_path,
                    USAGE_FIELDS,
                    {
                        "query_id": query_row["query_id"],
                        "query_variant": query_row["query_variant"],
                        "provider": provider,
                        "repetition": repetition,
                        "credits_consumed": reported_usage.get("credits_consumed", ""),
                        "num_search_queries": reported_usage.get("num_search_queries", ""),
                        "full_content_tokens": reported_usage.get("full_content_tokens", ""),
                        "cost_usd_reported": reported_usage.get("cost_usd_reported", ""),
                        "raw_response_path": str(raw_path.relative_to(out_dir)),
                    },
                )
                query_statuses.append(f"{provider}={status}/{len(results)}")
                if args.delay_ms:
                    time.sleep(args.delay_ms / 1000)

            if query_index == 1 or query_index % args.progress_every == 0 or query_index == len(queries):
                print(
                    f"progress {completed_requests}/{total_requests} "
                    f"query={query_row['query_id']} variant={query_row['query_variant']} "
                    + " ".join(query_statuses),
                    flush=True,
                )

    print(f"completed raw benchmark: {completed_requests} requests")
    print(f"runs: {run_path}")
    print(f"results: {result_path}")
    print(f"usage: {usage_path}")
    return 0


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", default=str(DEFAULT_QUERY_FILE))
    parser.add_argument("--out", required=True)
    parser.add_argument("--providers", default=",".join(DEFAULT_PROVIDERS), help="comma-separated provider names")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--include-reference-queries", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--max-queries", type=int, default=0, help="run only the first N primary query IDs")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--delay-ms", type=int, default=0)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--progress-every", type=int, default=5)
    args = parser.parse_args(list(argv))
    args.providers = [item.strip() for item in args.providers.split(",") if item.strip()]
    valid = set(DEFAULT_PROVIDERS)
    unknown = sorted(set(args.providers) - valid)
    if unknown:
        parser.error(f"unknown providers: {', '.join(unknown)}")
    if args.repetitions < 1:
        parser.error("--repetitions must be >= 1")
    return args


if __name__ == "__main__":
    sys.exit(run(parse_args(sys.argv[1:])))
