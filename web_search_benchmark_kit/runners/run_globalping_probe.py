#!/usr/bin/env python3
"""Probe unresolved benchmark URLs from an independent Globalping location.

Globalping limits unauthenticated callers to 250 measurements per hour. This
script keeps the request count bounded, writes each completed result as it
arrives, and never turns an access, server, or probe failure into a dead URL.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


API_ROOT = "https://api.globalping.io/v1"
USER_AGENT = "web-search-benchmark/1.0 (independent URL health check)"
TIMEOUT = 35
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

OUTPUT_FIELDS = [
    "original_url",
    "prior_classification",
    "providers",
    "occurrences",
    "globalping_create_status",
    "globalping_measurement_id",
    "globalping_measurement_status",
    "globalping_result_status",
    "globalping_status_code",
    "globalping_status_name",
    "globalping_final_url",
    "globalping_redirect_location",
    "globalping_body_bytes",
    "globalping_title",
    "globalping_probe_country",
    "globalping_probe_city",
    "globalping_probe_asn",
    "globalping_probe_network",
    "globalping_resolved_address",
    "globalping_total_ms",
    "globalping_api_cost",
    "globalping_api_remaining",
    "globalping_error",
    "globalping_classification",
    "globalping_observed_at",
]


def request_json(method: str, url: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, str], dict[str, Any] | None, str]:
    body = None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read()
            text = raw.decode("utf-8", errors="replace")
            return response.status, {key.lower(): value for key, value in response.headers.items()}, parse_json(text), ""
    except HTTPError as error:
        raw = error.read()
        text = raw.decode("utf-8", errors="replace")
        return error.code, {key.lower(): value for key, value in error.headers.items()}, parse_json(text), text[:500]
    except (TimeoutError, URLError, OSError) as error:
        return 0, {}, None, str(error)


def parse_json(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def int_value(value: Any) -> int:
    try:
        return int(str(value or ""))
    except (TypeError, ValueError):
        return 0


def title_from_body(body: Any) -> str:
    if not isinstance(body, str):
        return ""
    match = TITLE_RE.search(body)
    if not match:
        return ""
    return html.unescape(re.sub(r"\s+", " ", match.group(1))).strip()[:240]


def measurement_request(original_url: str) -> dict[str, Any]:
    parsed = urlsplit(original_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("unsupported URL scheme or missing hostname")
    path = parsed.path or "/"
    options: dict[str, Any] = {
        "request": {"method": "GET", "path": path},
        "protocol": parsed.scheme.upper(),
    }
    if parsed.query:
        options["request"]["query"] = parsed.query
    if parsed.port is not None and parsed.port != (443 if parsed.scheme.lower() == "https" else 80):
        options["port"] = parsed.port
    return {
        "type": "http",
        "target": parsed.hostname,
        "limit": 1,
        "measurementOptions": options,
    }


def priority(row: dict[str, str]) -> tuple[int, str]:
    # Independent probes are most useful for network/TLS/protocol residuals,
    # then for local access blocks. Keep the order deterministic for resume.
    weights = {
        "server_or_network_warning": 0,
        "reachable_tls_warning": 1,
        "reachable_alternate_network": 2,
        "reachable_transport_error": 3,
        "redirect_loop_or_timeout": 4,
        "other_broken": 5,
        "access_or_rate_limit_warning": 6,
    }
    return weights.get(row.get("final_classification", ""), 7), row.get("url", "")


def classify(status: int, result_status: str, error: str) -> str:
    if 200 <= status < 300:
        return "confirmed_get_ok"
    if 300 <= status < 400:
        return "confirmed_get_redirect"
    if status in {404, 410}:
        return "likely_dead_not_found"
    if status in {401, 403, 429}:
        return "access_or_rate_limit_warning"
    if 400 <= status < 500:
        return "other_broken"
    if 500 <= status < 600:
        return "server_or_network_warning"
    if result_status == "failed":
        return "globalping_probe_failed"
    if error:
        return "globalping_api_error"
    return "globalping_unresolved"


def create_measurement(row: dict[str, str]) -> dict[str, Any]:
    original_url = row["url"]
    result: dict[str, Any] = {field: "" for field in OUTPUT_FIELDS}
    result.update(
        {
            "original_url": original_url,
            "prior_classification": row.get("final_classification", ""),
            "providers": row.get("providers", ""),
            "occurrences": row.get("occurrences", ""),
            "globalping_observed_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    try:
        payload = measurement_request(original_url)
    except ValueError as error:
        result["globalping_classification"] = "globalping_unresolved"
        result["globalping_error"] = str(error)
        return result

    status, headers, data, error = request_json("POST", f"{API_ROOT}/measurements", payload)
    result["globalping_create_status"] = str(status or "")
    result["globalping_api_cost"] = headers.get("x-request-cost", "")
    result["globalping_api_remaining"] = headers.get("x-ratelimit-remaining", "")
    result["globalping_error"] = error
    if status == 429:
        result["globalping_classification"] = "globalping_rate_limited"
        return result
    if status != 202 or not data:
        api_error = data.get("error", {}) if data else {}
        result["globalping_error"] = str(api_error.get("message") or error or f"create HTTP {status}")
        result["globalping_classification"] = "globalping_api_error"
        return result

    measurement_id = str(data.get("id") or "")
    result["globalping_measurement_id"] = measurement_id
    if not measurement_id:
        result["globalping_classification"] = "globalping_api_error"
        result["globalping_error"] = "create response had no measurement id"
        return result
    return result


def poll_measurement(result: dict[str, Any]) -> dict[str, Any]:
    measurement_id = result.get("globalping_measurement_id", "")
    if not measurement_id:
        return result
    endpoint = f"{API_ROOT}/measurements/{measurement_id}"
    data: dict[str, Any] | None = None
    error = ""
    for attempt in range(45):
        status, _headers, data, error = request_json("GET", endpoint)
        if status == 200 and data:
            measurement_status = str(data.get("status", ""))
            result["globalping_measurement_status"] = measurement_status
            if measurement_status != "in-progress":
                break
        elif status == 429:
            time.sleep(min(2.0, 0.5 + attempt * 0.1))
            continue
        else:
            time.sleep(0.7)
            continue
        time.sleep(0.7)

    if not data:
        result["globalping_classification"] = "globalping_probe_failed"
        result["globalping_error"] = error or "no measurement response"
        return result
    measurements = data.get("results") or []
    if not measurements:
        result["globalping_classification"] = "globalping_probe_failed"
        result["globalping_error"] = "measurement completed without a probe result"
        return result
    probe_result = measurements[0] if isinstance(measurements[0], dict) else {}
    probe = probe_result.get("probe") or {}
    observed = probe_result.get("result") or {}
    result["globalping_result_status"] = str(observed.get("status", ""))
    result["globalping_status_code"] = str(observed.get("statusCode", ""))
    result["globalping_status_name"] = str(observed.get("statusCodeName", ""))
    result["globalping_probe_country"] = str(probe.get("country", ""))
    result["globalping_probe_city"] = str(probe.get("city", ""))
    result["globalping_probe_asn"] = str(probe.get("asn", ""))
    result["globalping_probe_network"] = str(probe.get("network", ""))
    result["globalping_resolved_address"] = str(observed.get("resolvedAddress", ""))
    timings = observed.get("timings") or {}
    result["globalping_total_ms"] = str(timings.get("total", ""))
    headers = observed.get("headers") or {}
    location = headers.get("location", "")
    if isinstance(location, list):
        location = location[0] if location else ""
    result["globalping_redirect_location"] = str(location)
    raw_body = observed.get("rawBody")
    if isinstance(raw_body, str):
        result["globalping_body_bytes"] = str(len(raw_body.encode("utf-8")))
        result["globalping_title"] = title_from_body(raw_body)
    status_code = int_value(observed.get("statusCode"))
    result["globalping_classification"] = classify(
        status_code,
        str(observed.get("status", "")),
        str(observed.get("error", "")),
    )
    return result


def append_row(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in OUTPUT_FIELDS})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-requests", type=int, default=249)
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument("--poll-workers", type=int, default=12)
    args = parser.parse_args()

    with args.results.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    eligible = [
        row
        for row in rows
        if row.get("final_classification") not in {"confirmed_get_ok", "confirmed_get_redirect", "likely_dead_not_found"}
    ]
    done: set[str] = set()
    if args.output.exists():
        with args.output.open(newline="", encoding="utf-8") as handle:
            done = {row.get("original_url", "") for row in csv.DictReader(handle)}
    pending = sorted((row for row in eligible if row.get("url") not in done), key=priority)
    if not pending:
        print("no pending URLs", file=sys.stderr)
        return
    budget = max(0, args.max_requests)
    created: list[dict[str, Any]] = []
    for index, row in enumerate(pending[:budget], start=1):
        result = create_measurement(row)
        append_row(args.output, result)
        if result.get("globalping_measurement_id"):
            created.append(result)
        print(
            f"created {index}/{min(len(pending), budget)} {row['url']} "
            f"status={result.get('globalping_create_status','')} remaining={result.get('globalping_api_remaining','')}",
            file=sys.stderr,
            flush=True,
        )
        if result.get("globalping_classification") == "globalping_rate_limited":
            break
        time.sleep(max(0.0, args.interval))

    # Rewrite completed rows in place after polling. The create rows are
    # already durable; a crash during polling therefore loses no measurement.
    completed: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, args.poll_workers)) as pool:
        futures = {pool.submit(poll_measurement, row): row["original_url"] for row in created}
        for count, future in enumerate(as_completed(futures), start=1):
            row = future.result()
            completed[row["original_url"]] = row
            print(
                f"polled {count}/{len(created)} {row['original_url']} "
                f"class={row.get('globalping_classification', '')} code={row.get('globalping_status_code', '')}",
                file=sys.stderr,
                flush=True,
            )

    if completed:
        existing: list[dict[str, str]] = []
        with args.output.open(newline="", encoding="utf-8") as handle:
            existing = list(csv.DictReader(handle))
        with args.output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            for row in existing:
                replacement = completed.get(row.get("original_url", ""))
                writer.writerow({field: (replacement or row).get(field, "") for field in OUTPUT_FIELDS})

    counts: dict[str, int] = {}
    for row in completed.values():
        key = str(row.get("globalping_classification", ""))
        counts[key] = counts.get(key, 0) + 1
    print(json.dumps({"created": len(created), "completed": len(completed), "classes": counts}, sort_keys=True))


if __name__ == "__main__":
    main()
