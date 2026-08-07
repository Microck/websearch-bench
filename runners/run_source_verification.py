#!/usr/bin/env python3
"""Verify selected result URLs through public source-specific endpoints.

This lane does not claim that a blocked HTML page was fetched successfully.
It records a separate source-level fact, such as an existing Stack Exchange
question, a Medium article present in its RSS feed, or a DOI present in
Crossref metadata.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


USER_AGENT = "web-search-benchmark/1.0 (source-level URL verification)"
TIMEOUT = 30
STACK_ID_RE = re.compile(r"/(?:questions|q)/(\d+)(?:/|$)", re.IGNORECASE)
DOI_RE = re.compile(r"/(10\.\d{4,9}/[^/?#]+)", re.IGNORECASE)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

OUTPUT_FIELDS = [
    "original_url",
    "source",
    "verification_kind",
    "source_endpoint",
    "source_http_status",
    "source_match",
    "source_resource_status",
    "source_id",
    "source_title",
    "source_url",
    "source_error",
    "source_observed_at",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def http_get(url: str, accept: str, timeout: int = TIMEOUT) -> tuple[int, str, bytes, str]:
    request = urllib.request.Request(
        url,
        headers={"Accept": accept, "User-Agent": USER_AGENT},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            return response.status, response.geturl(), body, ""
    except urllib.error.HTTPError as error:
        body = error.read()
        return error.code, error.geturl(), body, ""
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return 0, "", b"", str(error)[:500]


def blank_row(url: str, source: str, kind: str, endpoint: str) -> dict[str, str]:
    return {
        "original_url": url,
        "source": source,
        "verification_kind": kind,
        "source_endpoint": endpoint,
        "source_http_status": "",
        "source_match": "unresolved",
        "source_resource_status": "",
        "source_id": "",
        "source_title": "",
        "source_url": "",
        "source_error": "",
        "source_observed_at": now(),
    }


def stack_site_and_id(url: str) -> tuple[str, str] | None:
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname.lower() if parsed.hostname else ""
    if host == "stackoverflow.com":
        site = "stackoverflow"
    elif host.endswith(".stackexchange.com"):
        site = host.removesuffix(".stackexchange.com")
    else:
        return None
    match = STACK_ID_RE.search(parsed.path)
    if not match:
        return None
    return site, match.group(1)


def stack_rows(urls: list[str]) -> list[dict[str, str]]:
    grouped: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for url in urls:
        candidate = stack_site_and_id(url)
        if candidate:
            site, question_id = candidate
            grouped[site][question_id].append(url)

    results: list[dict[str, str]] = []
    for site, ids_by_url in sorted(grouped.items()):
        ids = sorted(ids_by_url, key=int)
        for start in range(0, len(ids), 100):
            chunk = ids[start : start + 100]
            query = urllib.parse.urlencode({"site": site, "pagesize": 100, "filter": "default"})
            endpoint = f"https://api.stackexchange.com/2.3/questions/{';'.join(chunk)}?{query}"
            status, final_url, body, error = http_get(endpoint, "application/json")
            items: dict[str, dict[str, object]] = {}
            if status == 200 and not error:
                try:
                    payload = json.loads(body.decode("utf-8"))
                    items = {
                        str(item.get("question_id")): item
                        for item in payload.get("items", [])
                        if isinstance(item, dict) and item.get("question_id") is not None
                    }
                except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                    error = "Stack Exchange API returned invalid JSON"
            for question_id in chunk:
                for url in ids_by_url[question_id]:
                    row = blank_row(url, "stackexchange", "question_api", endpoint)
                    row["source_http_status"] = str(status or "")
                    row["source_id"] = question_id
                    if error or status != 200:
                        row["source_error"] = error or f"Stack Exchange API HTTP {status}"
                    elif question_id in items:
                        item = items[question_id]
                        row["source_match"] = "confirmed"
                        row["source_resource_status"] = "exists"
                        row["source_title"] = str(item.get("title") or "")[:500]
                        row["source_url"] = str(item.get("link") or "")
                    else:
                        # The public API can omit deleted or hidden questions.
                        # This is evidence about the API, not a dead-URL claim.
                        row["source_match"] = "not_found_or_hidden"
                        row["source_resource_status"] = "not_returned_by_api"
                    row["source_endpoint"] = final_url or endpoint
                    results.append(row)
            time.sleep(0.25)
    return results


def medium_feed_for(url: str) -> str | None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.hostname != "medium.com":
        return None
    parts = [part for part in urllib.parse.unquote(parsed.path).split("/") if part]
    if not parts or parts[0] == "p":
        return None
    return "https://medium.com/feed/" + parts[0]


def clean_medium_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit(("https", host, path, "", ""))


def medium_item_links(body: bytes) -> dict[str, tuple[str, str]]:
    root = ET.fromstring(body)
    links: dict[str, tuple[str, str]] = {}
    for item in root.findall(".//item"):
        link = item.findtext("link") or ""
        title = item.findtext("title") or ""
        if link:
            links[clean_medium_url(link)] = (link, title.strip()[:500])
    return links


def medium_rows(urls: list[str]) -> list[dict[str, str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for url in urls:
        feed = medium_feed_for(url)
        if feed:
            grouped[feed].append(url)

    results: list[dict[str, str]] = []
    for endpoint, feed_urls in sorted(grouped.items()):
        status, final_url, body, error = http_get(endpoint, "application/rss+xml, application/xml, text/xml")
        links: dict[str, tuple[str, str]] = {}
        if status == 200 and not error:
            try:
                links = medium_item_links(body)
            except ET.ParseError:
                error = "Medium feed returned invalid XML"
        # Keep the append explicit so the URL order remains deterministic.
        for url in feed_urls:
            row = blank_row(url, "medium", "rss_article_match", endpoint)
            row["source_http_status"] = str(status or "")
            row["source_endpoint"] = final_url or endpoint
            match = links.get(clean_medium_url(url))
            if error or status != 200:
                row["source_error"] = error or f"Medium feed HTTP {status}"
            elif match:
                row["source_match"] = "confirmed"
                row["source_resource_status"] = "present_in_current_feed"
                row["source_url"] = match[0]
                row["source_title"] = match[1]
            else:
                row["source_match"] = "feed_available_no_exact_match"
                row["source_resource_status"] = "not_present_in_current_feed"
            results.append(row)
        time.sleep(0.25)
    return results


def doi_from_url(url: str) -> str | None:
    match = DOI_RE.search(urllib.parse.unquote(urllib.parse.urlsplit(url).path))
    if not match:
        return None
    return match.group(1).rstrip(".")


def doi_rows(urls: list[str]) -> list[dict[str, str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for url in urls:
        doi = doi_from_url(url)
        if doi:
            grouped[doi.lower()].append(url)

    results: list[dict[str, str]] = []
    for doi, doi_urls in sorted(grouped.items()):
        endpoint = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
        status, final_url, body, error = http_get(endpoint, "application/json")
        message: dict[str, object] = {}
        if status == 200 and not error:
            try:
                payload = json.loads(body.decode("utf-8"))
                message = payload.get("message") or {}
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                error = "Crossref returned invalid JSON"
        for url in doi_urls:
            row = blank_row(url, "crossref", "doi_metadata", endpoint)
            row["source_http_status"] = str(status or "")
            row["source_id"] = doi
            row["source_endpoint"] = final_url or endpoint
            if error or status != 200:
                row["source_error"] = error or f"Crossref HTTP {status}"
            elif message:
                row["source_match"] = "confirmed"
                row["source_resource_status"] = "doi_registered"
                titles = message.get("title") or []
                row["source_title"] = str(titles[0] if titles else "")[:500]
                row["source_url"] = str(message.get("URL") or "")
            else:
                row["source_match"] = "not_found"
                row["source_resource_status"] = "doi_not_registered"
            results.append(row)
        time.sleep(0.25)
    return results


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in OUTPUT_FIELDS} for row in rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()

    rows = read_csv(args.results)
    residual = [
        row["url"]
        for row in rows
        if row.get("final_classification") not in {"confirmed_get_ok", "confirmed_get_redirect", "likely_dead_not_found"}
    ]
    urls = list(dict.fromkeys(residual))
    output = stack_rows(urls) + medium_rows(urls) + doi_rows(urls)
    output.sort(key=lambda row: (row["original_url"], row["source"]))
    write_csv(args.output, output)
    counts = Counter(row["source_match"] for row in output)
    by_source = Counter((row["source"], row["source_match"]) for row in output)
    summary_rows = [
        {"source": source, "match": match, "rows": count}
        for (source, match), count in sorted(by_source.items())
    ]
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    with args.summary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source", "match", "rows"])
        writer.writeheader()
        writer.writerows(summary_rows)
    print(json.dumps({"residual_urls": len(urls), "verification_rows": len(output), "matches": dict(counts)}, indent=2))


if __name__ == "__main__":
    main()
