#!/usr/bin/env python3
"""Run ranked web-search benchmark requests using AI coding harnesses.

This runner is intentionally separate from ``run_benchmark.py`` and currently
supports only harness-based providers: ``codex`` and ``claude_code``.
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
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


DEFAULT_PROVIDERS = ("codex", "claude_code")
DEFAULT_QUERY_FILE = Path(__file__).resolve().parents[1] / "data" / "queries.csv"
MAX_RESULTS_PER_PROVIDER = 10
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_",
    "referrer",
}

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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def canonicalize_url(value: str) -> str:
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
        netloc = hostname

    query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lower_key = key.lower()
        if lower_key.startswith("utm_") or lower_key in TRACKING_QUERY_KEYS:
            continue
        query.append((key, value))

    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")

    return urlunsplit((scheme, netloc, path, urlencode(query, doseq=True), ""))


def classify_error(status: int, detail: str = "", fallback: str = "") -> str:
    lowered = detail.lower()
    if "not logged in" in lowered or "authentication" in lowered:
        return "auth_error"
    if "timeout" in lowered or status == 408:
        return "timeout"
    if status == 429:
        return "rate_limit"
    if status == 401:
        return "unauthorized"
    if 400 <= status < 500:
        return "http_4xx"
    if 500 <= status < 600:
        return "http_5xx"
    if detail:
        return fallback or "provider_error"
    return ""


def parse_json_bytes(body: bytes) -> Any | None:
    try:
        return json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def normalize_result_row(provider: str, item: dict[str, Any]) -> tuple[str, str, str, str, str]:
    url = str(item.get("url") or item.get("link") or item.get("href") or "")
    title = (
        str(item.get("title") or item.get("name") or item.get("heading") or "")
    )
    snippet = str(
        item.get("snippet")
        or item.get("text")
        or item.get("description")
        or item.get("highlight")
        or ""
    )
    published = str(
        item.get("published")
        or item.get("published_at")
        or item.get("publish_date")
        or item.get("date")
        or item.get("timestamp")
        or ""
    )
    if provider == "claude_code" and not snippet and isinstance(item.get("content"), str):
        snippet = str(item.get("content")).strip()
    return url, canonicalize_url(url), title, snippet, published


def append_csv(path: Path, fields: list[str], row: dict[str, Any]) -> None:
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in fields})


def load_queries(path: Path, include_reference_queries: bool) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows.append(
                {
                    "query_id": row["query_id"],
                    "category": row.get("category", ""),
                    "query_variant": "primary",
                    "query": row["query"],
                }
            )
            reference = (row.get("reference_query") or "").strip()
            if include_reference_queries and reference:
                rows.append(
                    {
                        "query_id": row["query_id"],
                        "category": row.get("category", ""),
                        "query_variant": "reference",
                        "query": reference,
                    }
                )
    return rows


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
        "provider_modes": {
            "codex": "codex exec --json",
            "claude_code": "claude --print --output-format stream-json --verbose",
        },
        "codex_api_key_from_env": bool(args.codex_api_key),
        "codex_base_url": args.codex_base_url,
        "claude_api_key_from_env": bool(args.claude_api_key),
        "claude_base_url": args.claude_base_url,
        "redirect_resolution": "not_performed; canonical URL normalization only",
        "run_type": "harness_only",
    }
    (out_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _extract_json_candidates(value: str) -> list[Any]:
    candidates: list[Any] = []

    parsed = parse_json_bytes(value.encode("utf-8"))
    if parsed is not None:
        candidates.append(parsed)
    for match in re.finditer(r"```json(.*?)```", value, flags=re.I | re.S):
        parsed = parse_json_bytes(match.group(1).encode("utf-8"))
        if parsed is not None:
            candidates.append(parsed)

    return candidates


def _clean_url(url: str) -> str:
    return url.strip().strip("`\"'.,)];:")


def _find_markdown_links(text: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    pattern = re.compile(r"\[([^\]]+)\]\((https?://[^)\]\s]+)\)")
    for match in pattern.finditer(text):
        title = match.group(1).strip()
        url = _clean_url(match.group(2).strip())
        if url.startswith(("http://", "https://")):
            results.append({"url": url, "title": title})
    return results


def _find_plain_urls(text: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    pattern = re.compile(r"\bhttps?://[^\s<>\[\]\(\)\"']+")
    for match in pattern.finditer(text):
        url = _clean_url(match.group(0))
        if url.startswith(("http://", "https://")):
            results.append({"url": url})
    return results


def _extract_container_results(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (dict, list)):
        return []

    results: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                results.extend(_extract_container_results(item))
        return results

    if not isinstance(value, dict):
        return []

    for key in ("results", "data", "items", "hits", "response", "matches"):
        candidate = value.get(key)
        if isinstance(candidate, list):
            for item in candidate:
                if isinstance(item, dict) and _extract_url(item) is not None:
                    results.append(item)
                elif isinstance(item, str):
                    results.append({"url": item})
            if results:
                return results

    for key, child in value.items():
        if isinstance(child, (dict, list)):
            child_results = _extract_container_results(child)
            if child_results:
                results.extend(child_results)

    return results


def _extract_url(item: dict[str, Any]) -> str | None:
    for key in ("url", "link", "href"):
        value = item.get(key)
        if isinstance(value, str):
            candidate = _clean_url(value)
            if candidate.startswith(("http://", "https://")):
                return candidate
    return None


def _extract_results_from_text(text: str, provider: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    for payload in _extract_json_candidates(text):
        out.extend(_extract_container_results(payload))

    out.extend(_find_markdown_links(text))
    out.extend(_find_plain_urls(text))

    if not out:
        for line in text.splitlines():
            line_text = line.strip()
            if line_text.startswith("{") and line_text.endswith("}"):
                payload = parse_json_bytes(line_text.encode("utf-8"))
                if isinstance(payload, dict):
                    out.extend(_extract_container_results(payload))
            if out:
                break

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in out:
        if isinstance(item, dict):
            url = _extract_url(item)
            if not url:
                continue
            canonical = canonicalize_url(url)
            if canonical in seen:
                continue
            seen.add(canonical)
            title = item.get("title")
            if title is None and "url" in item and "description" not in item:
                # Keep stable shape for later CSV writing.
                item["title"] = ""
            out_item = {
                "url": url,
                "title": title or item.get("name") or "",
                "snippet": item.get("snippet") or item.get("description") or "",
                "published": item.get("published") or item.get("date") or "",
            }
            deduped.append(out_item)
    return deduped


def parse_output_results(provider: str, text: str) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for line in text.splitlines():
        parsed_json = parse_json_bytes(line.encode("utf-8"))
        if not isinstance(parsed_json, dict):
            continue
        if "item" in parsed_json and isinstance(parsed_json["item"], dict):
            parsed.extend(_extract_results_from_text(json.dumps(parsed_json["item"]), provider))
        if "message" in parsed_json:
            parsed.extend(_extract_results_from_text(json.dumps(parsed_json["message"]), provider))
        if "content" in parsed_json:
            parsed.extend(_extract_results_from_text(json.dumps(parsed_json["content"]), provider))
        if "result" in parsed_json:
            parsed.extend(_extract_results_from_text(json.dumps(parsed_json["result"]), provider))
        if "text" in parsed_json:
            parsed.extend(_extract_results_from_text(str(parsed_json["text"]), provider))

        if "error" in parsed_json and isinstance(parsed_json["error"], str):
            parsed.extend(_extract_results_from_text(parsed_json["error"], provider))

    if not parsed:
        # Final fallback: parse full stream/text body directly.
        parsed.extend(_extract_results_from_text(text, provider))

    # Deduplicate and cap to top-10 while preserving order.
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in parsed:
        url = _extract_url(item)
        if not url:
            continue
        canonical = canonicalize_url(url)
        if canonical in seen:
            continue
        seen.add(canonical)
        deduped.append(item)
        if len(deduped) >= MAX_RESULTS_PER_PROVIDER:
            break
    return deduped


def collect_usage_and_error(result: dict[str, Any], command_name: str) -> dict[str, str]:
    usage: dict[str, str] = {}
    if not isinstance(result, dict):
        return usage

    if command_name == "claude_code":
        total_cost = result.get("total_cost_usd")
        if total_cost is not None:
            usage["cost_usd_reported"] = str(total_cost)
    return usage


def _find_status_error(result: dict[str, Any]) -> tuple[int, str]:
    status = 0
    detail = ""
    if not isinstance(result, dict):
        return status, detail

    if "status" in result and isinstance(result["status"], int):
        status = int(result["status"])
    elif "is_error" in result:
        if result.get("is_error"):
            terminal = result.get("terminal_reason", "") or result.get("type", "")
            detail = str(result.get("result", result.get("error", "")))
            if "api_error" in str(terminal):
                status = 401
            else:
                status = 1
    if "http_status" in result and isinstance(result["http_status"], int):
        status = int(result["http_status"])
    if result.get("error", ""):
        status = max(status, 1)
        detail = str(result.get("error"))
    return status, detail


def run_codex_query(
    query: str,
    timeout_seconds: float,
    model: str | None,
    openai_api_key: str | None,
    openai_base_url: str | None,
) -> tuple[int, float, bytes, str, list[dict[str, Any]], dict[str, str]]:
    prompt = (
        "Use the native web_search tool and return only the ranked results for this exact query. "
        "Do not rewrite the query. "
        f"query: {query!r}. "
        "Return as JSON list-style text with url, title, snippet, and published fields when possible."
    )
    command = ["codex", "exec", "--skip-git-repo-check", "--json"]
    if model:
        command.extend(["--model", model])
    command.append(prompt)
    env = os.environ.copy()
    if openai_api_key:
        env["OPENAI_API_KEY"] = openai_api_key
    if openai_base_url:
        env["OPENAI_BASE_URL"] = openai_base_url
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
        check=False,
        env=env,
    )
    latency_ms = (time.perf_counter() - started) * 1000
    stdout = completed.stdout
    stderr = completed.stderr.decode("utf-8", errors="replace")
    status = 200 if completed.returncode == 0 else 1
    text = stdout.decode("utf-8", errors="replace")
    results = parse_output_results("codex", text)
    if not status and not results:
        maybe_status, detail = _find_status_error(parse_json_bytes(completed.stdout) or {})
        if maybe_status:
            status = maybe_status
            if detail:
                stderr = detail
    return status, latency_ms, stdout, stderr, results, collect_usage_and_error({}, "codex")


def run_claude_code_query(
    query: str,
    timeout_seconds: float,
    model: str | None,
    anthropic_api_key: str | None,
    anthropic_base_url: str | None,
) -> tuple[int, float, bytes, str, list[dict[str, Any]], dict[str, str]]:
    prompt = (
        "Use the WebSearch tool for this exact query and return up to 10 ranked URLs. "
        "Do not rewrite the query. "
        f"{query!r}\n"
        "Output only a short ranked list."
    )
    command = ["claude", "--print", "--output-format", "stream-json", "--verbose"]
    if model:
        command.extend(["--model", model])
    command.append(prompt)
    env = os.environ.copy()
    if anthropic_api_key:
        env["ANTHROPIC_API_KEY"] = anthropic_api_key
        env["CLIPROXYAPI_API_KEY"] = anthropic_api_key
    if anthropic_base_url:
        env["ANTHROPIC_BASE_URL"] = anthropic_base_url
        env["CLIPROXYAPI_BASE_URL"] = anthropic_base_url
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
        check=False,
        env=env,
    )
    latency_ms = (time.perf_counter() - started) * 1000
    stdout = completed.stdout
    stderr = completed.stderr.decode("utf-8", errors="replace")
    text = stdout.decode("utf-8", errors="replace")
    status = 0 if completed.returncode == 0 else 1
    usage: dict[str, str] = {}
    results: list[dict[str, Any]] = []
    detail = ""

    seen_error = ""
    for line in text.splitlines():
        payload = parse_json_bytes(line.encode("utf-8"))
        if not isinstance(payload, dict):
            continue
        results.extend(parse_output_results("claude_code", json.dumps(payload)))
        if payload.get("is_error"):
            seen_error = str(payload.get("result", payload.get("error", "")))
        line_status, line_detail = _find_status_error(payload)
        if line_status:
            status = line_status
            if line_detail:
                detail = line_detail
        if "total_cost_usd" in payload:
            usage["cost_usd_reported"] = str(payload["total_cost_usd"])

    if not results:
        results = parse_output_results("claude_code", text)
    if status == 0 and seen_error:
        status = 1
        detail = seen_error
    return status, latency_ms, stdout, detail or stderr, results, usage


def _run_provider_query(
    provider: str,
    query_row: dict[str, str],
    timeout: float,
    codex_model: str | None,
    claude_model: str | None,
    codex_api_key: str | None,
    codex_base_url: str | None,
    claude_api_key: str | None,
    claude_base_url: str | None,
) -> tuple[int, float, bytes, str, list[dict[str, Any]], dict[str, str]]:
    query = query_row["query"]
    if provider == "codex":
        return run_codex_query(
            query,
            timeout,
            codex_model,
            codex_api_key,
            codex_base_url,
        )
    if provider == "claude_code":
        return run_claude_code_query(
            query,
            timeout,
            claude_model,
            claude_api_key,
            claude_base_url,
        )
    raise ValueError(f"unknown harness provider {provider!r}")


def run(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = out_dir / "raw"
    request_dir = out_dir / "requests"
    raw_dir.mkdir(exist_ok=True)
    request_dir.mkdir(exist_ok=True)

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

    for repetition in range(1, args.repetitions + 1):
        for query_index, query_row in enumerate(queries, start=1):
            provider_order = list(args.providers)
            rng.shuffle(provider_order)
            status_list: list[str] = []

            for provider in provider_order:
                completed_requests += 1
                started_at = utc_now()
                stem = f"{query_row['query_id']}__{query_row['query_variant']}"
                provider_raw = raw_dir / provider / f"rep_{repetition}"
                provider_requests = request_dir / provider / f"rep_{repetition}"
                provider_raw.mkdir(parents=True, exist_ok=True)
                provider_requests.mkdir(parents=True, exist_ok=True)
                raw_path = provider_raw / f"{stem}.jsonl"
                request_path = provider_requests / f"{stem}.json"

                status = 0
                latency_ms = 0.0
                body = b""
                detail = ""
                error_type = ""
                request_id = ""
                cost_usd = ""
                usage_payload: dict[str, str] = {}
                results: list[dict[str, Any]] = []

                request_payload = {
                    "provider": provider,
                    "query": query_row["query"],
                }
                if provider == "codex" and args.codex_model:
                    request_payload["model"] = args.codex_model
                if provider == "claude_code" and args.claude_model:
                    request_payload["model"] = args.claude_model
                request_path.write_text(
                    json.dumps(request_payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )

                try:
                    request_started = time.perf_counter()
                    status, latency_ms, body, detail, results, usage_payload = _run_provider_query(
                        provider=provider,
                        query_row=query_row,
                        timeout=args.timeout,
                        codex_model=args.codex_model,
                        claude_model=args.claude_model,
                        codex_api_key=args.codex_api_key,
                        codex_base_url=args.codex_base_url,
                        claude_api_key=args.claude_api_key,
                        claude_base_url=args.claude_base_url,
                    )
                    raw_path.write_bytes(body)
                except subprocess.TimeoutExpired as error:
                    latency_ms = (time.perf_counter() - request_started) * 1000
                    body = error.stdout or b""
                    detail = "timeout"
                    status = 408
                    raw_path.write_bytes(body)
                except Exception as error:  # keep the suite moving and record the failure
                    latency_ms = (time.perf_counter() - request_started) * 1000
                    detail = str(error)
                    status = 1
                    raw_path.write_bytes(detail.encode("utf-8", errors="replace"))

                if not error_type:
                    error_type = classify_error(status, detail or "", fallback="provider_error")
                if not results and not error_type:
                    error_type = "no_results"

                rows_written = 0
                for rank, item in enumerate(results[:MAX_RESULTS_PER_PROVIDER], start=1):
                    if not isinstance(item, dict):
                        continue
                    url, canonical_url, title, snippet, published_at = normalize_result_row(provider, item)
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
                    rows_written += 1

                if not cost_usd and usage_payload.get("cost_usd_reported"):
                    cost_usd = str(usage_payload["cost_usd_reported"])

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
                        "error_type": error_type if rows_written else error_type,
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
                        "credits_consumed": "",
                        "num_search_queries": "",
                        "full_content_tokens": "",
                        "cost_usd_reported": usage_payload.get("cost_usd_reported", ""),
                        "raw_response_path": str(raw_path.relative_to(out_dir)),
                    },
                )
                status_list.append(f"{provider}:{status}/{rows_written}")

                if args.delay_ms:
                    time.sleep(args.delay_ms / 1000)

            if query_index == 1 or query_index % args.progress_every == 0 or query_index == len(queries):
                print(
                    f"progress {completed_requests}/{total_requests} "
                    f"query={query_row['query_id']} variant={query_row['query_variant']} "
                    + " ".join(status_list),
                    flush=True,
                )

    print(f"completed harness benchmark: {completed_requests} requests")
    print(f"runs: {run_path}")
    print(f"results: {result_path}")
    print(f"usage: {usage_path}")
    return 0


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", default=str(DEFAULT_QUERY_FILE))
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--providers",
        default=",".join(DEFAULT_PROVIDERS),
        help="comma-separated provider names",
    )
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument(
        "--include-reference-queries",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--max-queries", type=int, default=0, help="run only the first N primary query IDs")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--delay-ms", type=int, default=0)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--progress-every", type=int, default=5)
    parser.add_argument("--codex-model")
    parser.add_argument("--claude-model")
    parser.add_argument("--codex-api-key", default=os.environ.get("OPENAI_API_KEY"))
    parser.add_argument("--codex-base-url", default=os.environ.get("OPENAI_BASE_URL"))
    parser.add_argument(
        "--claude-api-key",
        default=os.environ.get("CLIPROXYAPI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"),
    )
    parser.add_argument(
        "--claude-base-url",
        default=os.environ.get("CLIPROXYAPI_BASE_URL"),
        help="Anthropic-compatible endpoint; defaults to CLIPROXYAPI_BASE_URL when set.",
    )

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
