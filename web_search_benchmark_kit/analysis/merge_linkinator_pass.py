#!/usr/bin/env python3
"""Merge bounded URL-health follow-ups into one auditable corpus result."""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable


CLEAN_CLASSES = {"confirmed_get_ok", "confirmed_get_redirect"}
DEAD_CLASS = "likely_dead_not_found"

CLASS_ORDER = [
    "confirmed_get_ok",
    "confirmed_get_redirect",
    "reachable_tls_warning",
    "reachable_alternate_network",
    "reachable_transport_recovered",
    "reachable_transport_error",
    "redirect_loop_or_timeout",
    "access_or_rate_limit_warning",
    "server_or_network_warning",
    "likely_dead_not_found",
    "other_broken",
]

NORMAL_CURL_FILES = [
    "curl-chrome.csv",
    "curl-firefox.csv",
    "curl-ipv4-http1.csv",
    "curl-ipv6-http1.csv",
    "curl-final-retry-v2.csv",
    "curl-referer.csv",
    "curl-bing.csv",
]
BROWSER_FILES = [
    "browser-standard-v2.csv",
    "browser-long.csv",
    "browser-extended.csv",
]

# These phrases identify the response page, rather than a word that happened
# to occur in an otherwise valid page. Generic Cloudflare, captcha, and bot
# script markers are allowed when the title and body show the requested page.
BROWSER_BLOCK_RE = re.compile(
    r"just a moment|access denied|cloudflare|cf[-_]?chl|__cf_chl|/challenge|"
    r"verify you are human|robot|rate limit|429 too many|blocked due to excessive|"
    r"security check|security verification|undeclared automated|fw_error_www|"
    r"access restricted|403 forbidden|522: connection timed out|attention required|"
    r"researchgate - temporarily unavailable|unwanted direct access|"
    r"blocked by web application firewall|too many requests",
    re.IGNORECASE,
)
BODY_BLOCK_TITLE_RE = re.compile(
    r"just a moment|access denied|security verification|verify you are human|"
    r"attention required|request rate threshold exceeded|rate limit|"
    r"429 too many|blocked due to excessive|blocked by web application firewall|"
    r"undeclared automated|fw_error_www|access restricted|403 forbidden|"
    r"401 unauthorized|too many requests|researchgate - temporarily unavailable",
    re.IGNORECASE,
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def int_status(value: object) -> int:
    try:
        return int(str(value or ""))
    except ValueError:
        return 0


def acceptable_status(status: int) -> bool:
    return 200 <= status < 400 or status in {404, 410}


def clean_status(status: int) -> bool:
    return 200 <= status < 400


def status_class(original_url: str, status: int, final_url: str) -> str:
    if status in {404, 410}:
        return DEAD_CLASS
    if status >= 300 or (final_url and final_url != original_url):
        return "confirmed_get_redirect"
    return "confirmed_get_ok"


def evidence_text(method: str, status: int, final_url: str, title: str = "") -> str:
    parts = [method, f"HTTP {status}"]
    if final_url:
        parts.append(f"final={final_url}")
    if title:
        parts.append(f"title={title[:240]}")
    return " | ".join(parts)


def browser_evidence(pass_dir: Path) -> dict[str, dict[str, object]]:
    priority = {
        "browser-standard-v2.csv": 1,
        "browser-long.csv": 2,
        "browser-extended.csv": 3,
    }
    selected: dict[str, dict[str, object]] = {}
    for filename in BROWSER_FILES:
        for row in read_csv(pass_dir / filename):
            status = int_status(row.get("http_status"))
            if not acceptable_status(status) or row.get("error"):
                continue
            page_text = f"{row.get('page_title', '')} {row.get('final_url', '')}"
            if BROWSER_BLOCK_RE.search(page_text):
                continue
            url = row["original_url"]
            candidate = {
                "status": status,
                "final_url": row.get("final_url") or url,
                "method": filename.removesuffix(".csv"),
                "title": row.get("page_title", ""),
                "priority": priority[filename],
            }
            if url not in selected or candidate["priority"] > selected[url]["priority"]:
                selected[url] = candidate
    return selected


def body_probe_evidence(pass_dir: Path) -> dict[str, dict[str, object]]:
    files = ["curl-body-probe.csv", "curl-body-probe-v2.csv"]
    shell_urls: set[str] = set()
    rows: list[tuple[int, dict[str, str]]] = []
    for filename in files:
        version = 2 if filename.endswith("v2.csv") else 1
        for row in read_csv(pass_dir / filename):
            if row.get("markers") == "redirect_shell":
                shell_urls.add(row["original_url"])
            rows.append((version, row))

    selected: dict[str, dict[str, object]] = {}
    for version, row in rows:
        url = row["original_url"]
        if url in shell_urls or row.get("error"):
            continue
        status = int_status(row.get("observed_status"))
        if not acceptable_status(status) or BODY_BLOCK_TITLE_RE.search(row.get("title", "")):
            continue
        size = int_status(row.get("download_bytes") or row.get("body_bytes_examined"))
        content_type = row.get("content_type", "").lower()
        # A tiny title-less HTML response is commonly a redirect shell. Binary
        # files and larger title-less responses remain valid GET evidence.
        if not row.get("title") and content_type.startswith("text/html") and size < 1000:
            continue
        candidate = {
            "status": status,
            "final_url": row.get("final_url") or url,
            "method": f"{Path('curl-body-probe-v2.csv' if version == 2 else 'curl-body-probe.csv').stem}",
            "title": row.get("title", ""),
            "priority": (version, size),
            "markers": row.get("markers", ""),
        }
        if url not in selected or candidate["priority"] > selected[url]["priority"]:
            selected[url] = candidate
    return selected


def medbox_evidence(pass_dir: Path) -> dict[str, dict[str, object]]:
    """Read the two fresh live body checks that followed the retry pass."""
    path = pass_dir / "medbox-live-body-probe.csv"
    if not path.exists():
        return {}
    selected: dict[str, dict[str, object]] = {}
    for row in read_csv(path):
        status = int_status(row.get("observed_status"))
        if not clean_status(status) or row.get("error"):
            continue
        url = row["original_url"]
        selected[url] = {
            "status": status,
            "final_url": row.get("final_url") or url,
            "method": "medbox-live-body-probe",
            "title": row.get("title", ""),
            "priority": (3, int_status(row.get("download_bytes"))),
        }
    return selected


def agy_evidence(pass_dir: Path, filename: str = "agy-gemini-3.6-flash.csv") -> dict[str, dict[str, object]]:
    """Read strict clean evidence from the Gemini 3.6 Flash retry lane.

    The Gemini checker also records transport and TLS diagnostics. Only rows
    that completed with a clean 2xx/3xx response are eligible here. A small,
    title-less HTML response is rejected because the earlier body probe found
    that CodeProject serves a 114-byte redirect shell for one of these URLs.
    """
    path = pass_dir / filename
    if not path.exists():
        return {}

    selected: dict[str, dict[str, object]] = {}
    allowed = {
        "confirmed_get_ok",
        "confirmed_get_redirect",
        "reachable_transport_recovered",
    }
    for row in read_csv(path):
        if row.get("classification") not in allowed:
            continue
        status = int_status(row.get("observed_status"))
        if not clean_status(status):
            continue
        title = row.get("page_title", "").strip()
        if title.lower() in {"n/a", "na", "none"}:
            title = ""
        content_type = row.get("content_type", "").lower()
        size = int_status(row.get("body_bytes"))
        page_text = f"{title} {row.get('evidence', '')}"
        if BROWSER_BLOCK_RE.search(page_text):
            continue
        if not title and content_type.startswith("text/html") and size < 1000:
            continue
        url = row["original_url"]
        candidate = {
            "status": status,
            "final_url": row.get("final_url") or url,
            "method": "agy-gemini-3.6-flash",
            "title": title,
            "priority": (size, int_status(row.get("redirect_count"))),
            "evidence": row.get("evidence", ""),
        }
        current = selected.get(url)
        if current is None or candidate["priority"] > current["priority"]:
            selected[url] = candidate
    return selected


def globalping_evidence(pass_dir: Path) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, str]]]:
    """Read strict GET evidence from the independent Globalping lane.

    Globalping reports a real HTTP status from an external probe. Explicit
    2xx/3xx and 404/410 responses are eligible for promotion. Probe failures,
    403/429 responses, and 5xx responses remain warnings. A title-less HTML
    body under 1000 bytes is excluded because the benchmark has already
    observed that shape as a redirect shell.
    """
    path = pass_dir / "globalping-http.csv"
    if not path.exists():
        return {}, {}
    selected: dict[str, dict[str, object]] = {}
    rows_by_url: dict[str, dict[str, str]] = {}
    for row in read_csv(path):
        url = row.get("original_url", "")
        if not url:
            continue
        rows_by_url[url] = row
        status = int_status(row.get("globalping_status_code"))
        classification = row.get("globalping_classification", "")
        if classification not in {"confirmed_get_ok", "confirmed_get_redirect", "likely_dead_not_found"}:
            continue
        title = row.get("globalping_title", "").strip()
        body_bytes = int_status(row.get("globalping_body_bytes"))
        if BROWSER_BLOCK_RE.search(title):
            continue
        if 200 <= status < 300 and not title and body_bytes < 1000:
            continue
        candidate = {
            "status": status,
            "final_url": row.get("globalping_redirect_location") or url,
            "method": "globalping-http-get",
            "title": title,
            "priority": (body_bytes, int_status(row.get("globalping_total_ms"))),
            "evidence": (
                f"Globalping probe {row.get('globalping_probe_city', '')}, "
                f"{row.get('globalping_probe_country', '')}: HTTP {status}"
            ).strip(),
        }
        if classification == DEAD_CLASS:
            candidate["final_url"] = url
        current = selected.get(url)
        if current is None or candidate["priority"] > current["priority"]:
            selected[url] = candidate
    return selected, rows_by_url


def diagnostic_evidence(pass_dir: Path) -> dict[str, dict[str, dict[str, object]]]:
    groups: dict[str, dict[str, dict[str, object]]] = {
        "tls": {},
        "alternate": {},
    }

    def add(group: str, url: str, method: str, status: int, final_url: str, title: str = "") -> None:
        current = groups[group].get(url)
        candidate = {
            "status": status,
            "final_url": final_url or url,
            "method": method,
            "title": title,
        }
        if current is None or status > int(current["status"]):
            groups[group][url] = candidate

    for row in read_csv(pass_dir / "curl-insecure-http1.csv"):
        status = int_status(row.get("http_status"))
        if clean_status(status) and not row.get("error"):
            add("tls", row["original_url"], "curl-insecure-http1", status, row.get("final_url", ""))
    for row in read_csv(pass_dir / "browser-insecure-v2.csv"):
        status = int_status(row.get("http_status"))
        page_text = f"{row.get('page_title', '')} {row.get('final_url', '')}"
        if clean_status(status) and not row.get("error") and not BROWSER_BLOCK_RE.search(page_text):
            add("tls", row["original_url"], "browser-insecure-v2", status, row.get("final_url", ""), row.get("page_title", ""))
    for row in read_csv(pass_dir / "curl-scheme-swap.csv"):
        status = int_status(row.get("http_status"))
        if clean_status(status) and not row.get("error"):
            add("tls", row["original_url"], "curl-scheme-swap", status, row.get("final_url", ""))
    for filename, group_name in (("dns-fanout-ipv4.csv", "alternate"), ("dns-fanout-ipv6.csv", "alternate")):
        for row in read_csv(pass_dir / filename):
            status = int_status(row.get("http_status"))
            if clean_status(status) and not row.get("error"):
                add(group_name, row["original_url"], filename.removesuffix(".csv"), status, row.get("final_url", ""))
    return groups


def all_pass3_rows(pass_dir: Path) -> dict[str, list[dict[str, str]]]:
    files = NORMAL_CURL_FILES + [
        "curl-insecure-http1.csv",
        "curl-scheme-swap.csv",
        "browser-standard-v2.csv",
        "browser-long.csv",
        "browser-extended.csv",
        "browser-insecure-v2.csv",
        "dns-fanout-ipv4.csv",
        "dns-fanout-ipv6.csv",
    ]
    rows: dict[str, list[dict[str, str]]] = defaultdict(list)
    for filename in files:
        path = pass_dir / filename
        if not path.exists():
            continue
        for row in read_csv(path):
            url = row.get("original_url", "")
            if url:
                rows[url].append(row)
    return rows


def choose_residual_classification(
    prior: str,
    rows: Iterable[dict[str, str]],
) -> tuple[str, str, str]:
    statuses = [int_status(row.get("http_status") or row.get("observed_status")) for row in rows]
    errors = " ".join(row.get("error", "") for row in rows).lower()
    titles = " ".join(row.get("page_title", "") for row in rows).lower()
    if any(status in {401, 403, 429} for status in statuses) or any(
        phrase in titles for phrase in ("access denied", "just a moment", "cloudflare", "rate limit", "security check")
    ):
        return "access_or_rate_limit_warning", "", "access or rate-limit response remained after browser and header passes"
    if "curl_exit_47" in errors or "maximum (10) redirects" in errors:
        return "redirect_loop_or_timeout", "", "redirect chain did not settle within the bounded retries"
    if any(500 <= status <= 599 for status in statuses) or any(
        token in errors for token in ("curl_exit_", "timeout", "timed out", "could not resolve", "connection refused")
    ):
        return "server_or_network_warning", "", "no clean response; remaining evidence was a server or network failure"
    if any(400 <= status < 500 for status in statuses):
        return "other_broken", next((str(status) for status in statuses if 400 <= status < 500), ""), "non-success client status remained"
    return prior, "", "no additional clean or dead evidence was observed"


def write_summary(out_dir: Path, rows: list[dict[str, str]]) -> Counter[str]:
    counts = Counter(row["final_classification"] for row in rows)
    total = len(rows)
    with (out_dir / "consolidated-summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["classification", "urls", "percent"])
        for classification in CLASS_ORDER:
            count = counts.get(classification, 0)
            writer.writerow([classification, count, f"{count / total * 100:.2f}"])
    return counts


def write_provider_summary(out_dir: Path, rows: list[dict[str, str]], counts: Counter[str]) -> None:
    providers = sorted({provider for row in rows for provider in row["providers"].split(";") if provider})
    fields = ["provider", "unique_urls"] + CLASS_ORDER + ["confirmed_get", "confirmed_pct"]
    with (out_dir / "consolidated-provider-summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for provider in providers:
            selected = [row for row in rows if provider in row["providers"].split(";")]
            provider_counts = Counter(row["final_classification"] for row in selected)
            confirmed = sum(provider_counts.get(name, 0) for name in CLEAN_CLASSES)
            output = {"provider": provider, "unique_urls": len(selected)}
            output.update({name: provider_counts.get(name, 0) for name in CLASS_ORDER})
            output["confirmed_get"] = confirmed
            output["confirmed_pct"] = f"{confirmed / len(selected) * 100:.2f}" if selected else "0.00"
            writer.writerow(output)


def write_flagged(out_dir: Path, rows: list[dict[str, str]]) -> None:
    flagged = [row for row in rows if row["final_classification"] not in CLEAN_CLASSES]
    fields = [
        "final_classification",
        "final_status",
        "providers",
        "occurrences",
        "url",
        "final_method",
        "final_reason",
        "pass3_attempt_count",
        "pass3_clean_evidence",
        "pass3_insecure_evidence",
        "agy_classification",
        "agy_status",
        "agy_final_url",
        "agy_title",
        "agy_body_bytes",
        "agy_evidence",
        "agy_pass2_classification",
        "agy_pass2_status",
        "agy_pass2_final_url",
        "agy_pass2_title",
        "agy_pass2_body_bytes",
        "agy_pass2_evidence",
        "globalping_classification",
        "globalping_create_status",
        "globalping_measurement_status",
        "globalping_result_status",
        "globalping_status_code",
        "globalping_status_name",
        "globalping_probe_country",
        "globalping_probe_city",
        "globalping_probe_network",
        "globalping_total_ms",
        "globalping_error",
        "globalping_title",
        "globalping_body_bytes",
    ]
    with (out_dir / "consolidated-flagged.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in flagged)

    with (out_dir / "consolidated-flagged.html").open("w", encoding="utf-8") as handle:
        handle.write("<!doctype html><meta charset=\"utf-8\"><title>Pass 3 URL health follow-ups</title>")
        handle.write("<style>body{font:14px system-ui,sans-serif;margin:2rem}table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccc;padding:.35rem;text-align:left;vertical-align:top}th{position:sticky;top:0;background:#eee}a{overflow-wrap:anywhere}</style>")
        handle.write("<h1>Pass 3 URL health follow-ups</h1><p>Rows below were not confirmed as a normal GET success or redirect. 404/410 rows are retained as likely dead. Access, server, TLS, and protocol warnings are not treated as dead.</p><table><thead><tr><th>classification</th><th>status</th><th>providers</th><th>occurrences</th><th>url</th><th>method</th><th>reason</th></tr></thead><tbody>")
        for row in flagged:
            url = html.escape(row["url"], quote=True)
            handle.write(
                "<tr>"
                f"<td>{html.escape(row['final_classification'])}</td>"
                f"<td>{html.escape(row['final_status'])}</td>"
                f"<td>{html.escape(row['providers'])}</td>"
                f"<td>{html.escape(row['occurrences'])}</td>"
                f"<td><a href=\"{url}\">{url}</a></td>"
                f"<td>{html.escape(row['final_method'])}</td>"
                f"<td>{html.escape(row['final_reason'])}</td>"
                "</tr>"
            )
        handle.write("</tbody></table>")


def write_chart(out_dir: Path, counts: Counter[str]) -> None:
    import matplotlib.pyplot as plt

    display = {
        "confirmed_get_ok": "GET OK",
        "confirmed_get_redirect": "GET redirect",
        "reachable_tls_warning": "TLS-only reachability",
        "reachable_alternate_network": "Alternate-network reachability",
        "reachable_transport_recovered": "Transport recovered",
        "reachable_transport_error": "Transport error",
        "redirect_loop_or_timeout": "Redirect loop/timeout",
        "access_or_rate_limit_warning": "Access/rate warning",
        "server_or_network_warning": "Server/network warning",
        "likely_dead_not_found": "404/410 likely dead",
        "other_broken": "Other HTTP error",
    }
    names = [name for name in CLASS_ORDER if counts.get(name)]
    labels = [display[name] for name in names]
    values = [counts[name] for name in names]
    colors = [
        "#197c5a" if name in CLEAN_CLASSES else "#d6a84f" if name.startswith("reachable_") else "#d95f59" if name in {"access_or_rate_limit_warning", "server_or_network_warning"} else "#68758a"
        for name in names
    ]
    fig, axis = plt.subplots(figsize=(11, 6.5))
    bars = axis.barh(labels[::-1], values[::-1], color=colors[::-1])
    axis.set_title("Full-corpus URL health after bounded follow-up passes")
    axis.set_xlabel("normalized URLs")
    axis.grid(axis="x", alpha=0.2)
    axis.set_axisbelow(True)
    axis.set_xlim(0, max(values) * 1.08)
    for bar, value in zip(bars, values[::-1]):
        axis.text(value + max(values) * 0.01, bar.get_y() + bar.get_height() / 2, f"{value:,}", va="center", fontsize=9)
    fig.tight_layout()
    fig.savefig(out_dir / "consolidated-health.png", dpi=160)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True, help="full-pass-2 directory")
    parser.add_argument("--pass3", type=Path, required=True, help="full-pass-3 directory")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    rows = read_csv(args.base / "consolidated-results.csv")
    prior_by_url = {row["url"]: row for row in rows}
    retry_urls = {
        row["url"]
        for row in rows
        if row["final_classification"] not in CLEAN_CLASSES | {DEAD_CLASS}
    }
    browser = browser_evidence(args.pass3)
    body = body_probe_evidence(args.pass3)
    manual = medbox_evidence(args.pass3)
    agy = agy_evidence(args.pass3)
    agy_pass2 = agy_evidence(args.pass3, "agy-gemini-3.6-flash-pass2.csv")
    globalping, globalping_rows_by_url = globalping_evidence(args.pass3)
    agy_rows_by_url = {}
    agy_path = args.pass3 / "agy-gemini-3.6-flash.csv"
    if agy_path.exists():
        agy_rows_by_url = {row["original_url"]: row for row in read_csv(agy_path)}
    agy_pass2_rows_by_url = {}
    agy_pass2_path = args.pass3 / "agy-gemini-3.6-flash-pass2.csv"
    if agy_pass2_path.exists():
        agy_pass2_rows_by_url = {row["original_url"]: row for row in read_csv(agy_pass2_path)}
    diagnostics = diagnostic_evidence(args.pass3)
    pass3_rows = all_pass3_rows(args.pass3)

    # The body probe is the gate for curl-only promotion. Browser and live
    # body evidence are disjoint in this run, but the precedence keeps the
    # merge deterministic if a later run overlaps them.
    clean_evidence: dict[str, dict[str, object]] = {}
    dead_evidence: dict[str, dict[str, object]] = {}
    for url in retry_urls:
        candidates = []
        if url in browser:
            candidates.append(browser[url])
        if url in body:
            candidates.append(body[url])
        if url in manual:
            candidates.append(manual[url])
        if url in agy:
            candidates.append(agy[url])
        if url in agy_pass2:
            candidates.append(agy_pass2[url])
        if url in globalping:
            candidates.append(globalping[url])
        for candidate in candidates:
            status = int(candidate["status"])
            if status in {404, 410}:
                dead_evidence.setdefault(url, candidate)
            elif clean_status(status):
                clean_evidence.setdefault(url, candidate)
    for url in list(dead_evidence):
        if url in clean_evidence:
            del dead_evidence[url]

    tls_only = {url: value for url, value in diagnostics["tls"].items() if url in retry_urls and url not in clean_evidence and url not in dead_evidence}
    alternate_only = {url: value for url, value in diagnostics["alternate"].items() if url in retry_urls and url not in clean_evidence and url not in dead_evidence}

    for row in rows:
        url = row["url"]
        prior_class = row["final_classification"]
        row["pass2_classification"] = prior_class
        row["pass2_status"] = row.get("final_status", "")
        row["pass2_url"] = row.get("final_url", "")
        row["pass2_method"] = row.get("final_method", "")
        row["pass2_reason"] = row.get("final_reason", "")
        row["pass3_attempt_count"] = str(len(pass3_rows.get(url, []))) if url in retry_urls else "0"
        row["pass3_clean_evidence"] = ""
        row["pass3_insecure_evidence"] = ""
        row["pass3_classification"] = prior_class
        row["pass3_reason"] = ""
        agy_row = agy_rows_by_url.get(url)
        row["agy_classification"] = agy_row.get("classification", "") if agy_row else ""
        row["agy_status"] = agy_row.get("observed_status", "") if agy_row else ""
        row["agy_final_url"] = agy_row.get("final_url", "") if agy_row else ""
        row["agy_title"] = agy_row.get("page_title", "") if agy_row else ""
        row["agy_body_bytes"] = agy_row.get("body_bytes", "") if agy_row else ""
        row["agy_evidence"] = agy_row.get("evidence", "") if agy_row else ""
        agy_pass2_row = agy_pass2_rows_by_url.get(url)
        row["agy_pass2_classification"] = agy_pass2_row.get("classification", "") if agy_pass2_row else ""
        row["agy_pass2_status"] = agy_pass2_row.get("observed_status", "") if agy_pass2_row else ""
        row["agy_pass2_final_url"] = agy_pass2_row.get("final_url", "") if agy_pass2_row else ""
        row["agy_pass2_title"] = agy_pass2_row.get("page_title", "") if agy_pass2_row else ""
        row["agy_pass2_body_bytes"] = agy_pass2_row.get("body_bytes", "") if agy_pass2_row else ""
        row["agy_pass2_evidence"] = agy_pass2_row.get("evidence", "") if agy_pass2_row else ""
        globalping_row = globalping_rows_by_url.get(url)
        for field in (
            "globalping_classification",
            "globalping_create_status",
            "globalping_measurement_status",
            "globalping_result_status",
            "globalping_status_code",
            "globalping_status_name",
            "globalping_probe_country",
            "globalping_probe_city",
            "globalping_probe_network",
            "globalping_total_ms",
            "globalping_error",
            "globalping_title",
            "globalping_body_bytes",
        ):
            row[field] = globalping_row.get(field, "") if globalping_row else ""

        if url in clean_evidence:
            evidence = clean_evidence[url]
            status = int(evidence["status"])
            final_url = str(evidence["final_url"])
            classification = status_class(url, status, final_url)
            row["final_classification"] = classification
            row["final_status"] = str(status)
            row["final_url"] = final_url
            row["final_method"] = str(evidence["method"])
            row["final_reason"] = "pass 3 validated the requested page or file; challenge and redirect-shell pages were excluded"
            row["pass3_clean_evidence"] = evidence.get("evidence") or evidence_text(str(evidence["method"]), status, final_url, str(evidence.get("title", "")))
        elif url in dead_evidence:
            evidence = dead_evidence[url]
            status = int(evidence["status"])
            row["final_classification"] = DEAD_CLASS
            row["final_status"] = str(status)
            row["final_url"] = str(evidence["final_url"])
            row["final_method"] = str(evidence["method"])
            row["final_reason"] = "pass 3 observed a validated HTTP 404/410 response"
        elif url in tls_only:
            evidence = tls_only[url]
            row["final_classification"] = "reachable_tls_warning"
            row["final_status"] = str(evidence["status"])
            row["final_url"] = str(evidence["final_url"])
            row["final_method"] = str(evidence["method"])
            row["final_reason"] = "normal TLS evidence did not settle, but a bounded insecure or scheme diagnostic returned 2xx/3xx"
            row["pass3_insecure_evidence"] = evidence_text(str(evidence["method"]), int(evidence["status"]), str(evidence["final_url"]), str(evidence.get("title", "")))
        elif url in alternate_only:
            evidence = alternate_only[url]
            row["final_classification"] = "reachable_alternate_network"
            row["final_status"] = str(evidence["status"])
            row["final_url"] = str(evidence["final_url"])
            row["final_method"] = str(evidence["method"])
            row["final_reason"] = "a direct alternate-IP path returned 2xx/3xx, but the normal path did not settle"
        elif url in retry_urls:
            # These labels already encode a positive diagnostic result from
            # pass 2. Keep that evidence unless pass 3 supplied a stronger
            # normal GET, dead status, or a dedicated diagnostic promotion.
            if prior_class in {
                "reachable_tls_warning",
                "reachable_alternate_network",
                "reachable_transport_recovered",
                "reachable_transport_error",
                "redirect_loop_or_timeout",
                "other_broken",
            }:
                row["final_reason"] = (row.get("final_reason", "") + "; pass 3 did not produce stronger normal or dead evidence").strip("; ")
                row["pass3_reason"] = "prior diagnostic classification retained"
            else:
                classification, status, reason = choose_residual_classification(prior_class, pass3_rows.get(url, []))
                row["final_classification"] = classification
                row["final_status"] = status or row.get("final_status", "")
                row["final_method"] = "pass3-residual"
                row["final_reason"] = reason
                row["pass3_reason"] = reason
        row["pass3_classification"] = row["final_classification"]

    # Keep the historical field order first, then append the pass-2/pass-3
    # audit fields. This preserves consumers that read the old columns.
    original_fields = list(read_csv(args.base / "consolidated-results.csv")[0].keys())
    appended = [
        "pass2_classification",
        "pass2_status",
        "pass2_url",
        "pass2_method",
        "pass2_reason",
        "pass3_classification",
        "pass3_reason",
        "pass3_attempt_count",
        "pass3_clean_evidence",
        "pass3_insecure_evidence",
        "agy_classification",
        "agy_status",
        "agy_final_url",
        "agy_title",
        "agy_body_bytes",
        "agy_evidence",
        "agy_pass2_classification",
        "agy_pass2_status",
        "agy_pass2_final_url",
        "agy_pass2_title",
        "agy_pass2_body_bytes",
        "agy_pass2_evidence",
        "globalping_classification",
        "globalping_create_status",
        "globalping_measurement_status",
        "globalping_result_status",
        "globalping_status_code",
        "globalping_status_name",
        "globalping_probe_country",
        "globalping_probe_city",
        "globalping_probe_network",
        "globalping_total_ms",
        "globalping_error",
        "globalping_title",
        "globalping_body_bytes",
    ]
    with (args.out / "consolidated-results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=original_fields + appended)
        writer.writeheader()
        writer.writerows(rows)

    counts = write_summary(args.out, rows)
    write_provider_summary(args.out, rows, counts)
    write_flagged(args.out, rows)
    write_chart(args.out, counts)
    payload = {
        "tool": "linkinator-plus-bounded-get-followups",
        "linkinator_version": "8.0.3",
        "unique_urls": len(rows),
        "retry_urls": len(retry_urls),
        "classification_counts": dict(counts),
        "method": [
            "The full-pass-2 result covered all 3,579 normalized URLs before this targeted loop.",
            "Pass 3 retried the 677 rows that were neither normal clean nor repeated 404/410.",
            "Chrome and Firefox curl headers, IPv4/IPv6 HTTP/1.1, bounded curl retries, referers, scheme swaps, DNS fan-out, and fresh Chromium navigation were used as separate evidence lanes.",
            "Curl 2xx/3xx promotions required a body probe with a real page/file title or substantial non-shell body. Explicit challenge, access-denied, rate-limit, and tiny redirect-shell responses were excluded.",
            "A generic Cloudflare, captcha, or bot script marker was not sufficient to reject a response when the requested page title and body were present.",
            "Insecure TLS and scheme-only successes remain diagnostics, not normal GET success.",
            "Gemini 3.6 Flash ran a bounded concurrent curl retry over the 500 residual URLs. Only non-challenge 2xx/3xx responses with a substantial body or real title were promoted; TLS-only, transport, and access responses remain diagnostics or warnings.",
            "A second Gemini 3.6 Flash pass then retried the 475 rows that remained non-clean or non-dead, using HTTP/1.1, IPv4, alternate headers, and HTTPS variants. The same strict promotion gate was applied.",
            "Globalping ran two bounded lanes totaling 473 unauthenticated GET measurements from independent public probes, covering every residual URL. Only explicit external 2xx/3xx and 404/410 responses were promoted. 403/429, 5xx, and probe failures remain warnings.",
            "A 21-URL Globalping recheck retried every probe failure and server response from those two lanes. Two additional clean responses were promoted; the remaining probe failures and 5xx responses remain warnings.",
            "The interrupted headed-browser experiment is not counted as evidence.",
        ],
    }
    (args.out / "consolidated.json").write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(json.dumps({"counts": counts, "retry_urls": len(retry_urls), "clean_promotions": len(clean_evidence), "dead_promotions": len(dead_evidence), "tls_only": len(tls_only)}, indent=2))


if __name__ == "__main__":
    main()
