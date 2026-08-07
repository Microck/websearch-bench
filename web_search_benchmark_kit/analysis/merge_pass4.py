#!/usr/bin/env python3
"""Merge source-level and Tailscale remote URL evidence into pass 4."""

from __future__ import annotations

import argparse
import csv
import html
import json
from collections import Counter, defaultdict
from pathlib import Path

try:
    from merge_linkinator_pass import (
        CLASS_ORDER,
        CLEAN_CLASSES,
        DEAD_CLASS,
        read_csv,
        write_chart,
        write_provider_summary,
        write_summary,
    )
except ImportError:  # pragma: no cover - supports package-style imports
    from .merge_linkinator_pass import (
        CLASS_ORDER,
        CLEAN_CLASSES,
        DEAD_CLASS,
        read_csv,
        write_chart,
        write_provider_summary,
        write_summary,
    )


def as_int(value: object) -> int:
    try:
        return int(str(value or ""))
    except ValueError:
        return 0


def remote_label(path: Path) -> str:
    return path.stem


def read_remote(path: Path) -> dict[str, list[dict[str, str]]]:
    by_url: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_csv(path):
        if row.get("original_url"):
            by_url[row["original_url"]].append(row)
    return by_url


def read_source(path: Path) -> dict[str, list[dict[str, str]]]:
    by_url: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_csv(path):
        if row.get("original_url"):
            by_url[row["original_url"]].append(row)
    return by_url


def distinct(values: list[str]) -> str:
    return ";".join(dict.fromkeys(value for value in values if value))


def remote_summary(rows: list[dict[str, str]]) -> dict[str, str]:
    clean = [
        row
        for row in rows
        if row.get("classification") == "remote_clean_candidate"
        and row.get("body_valid") == "yes"
        and 200 <= as_int(row.get("http_status")) < 400
    ]
    dead = [
        row
        for row in rows
        if row.get("classification") == "remote_likely_dead_candidate"
        and as_int(row.get("http_status")) in {404, 410}
    ]
    clean.sort(key=lambda row: (-as_int(row.get("body_bytes")), row.get("remote_hostname", "")))
    peers = distinct([row.get("remote_hostname", "") for row in rows])
    clean_peers = distinct([row.get("remote_hostname", "") for row in clean])
    evidence = []
    for row in sorted(clean, key=lambda item: (-as_int(item.get("body_bytes")), item.get("remote_hostname", ""))):
        title = row.get("page_title", "").replace("\n", " ")[:140]
        evidence.append(
            f"{row.get('remote_hostname', '')}: HTTP {row.get('http_status', '')} "
            f"body={row.get('body_bytes', '')} final={row.get('final_url', '')} title={title}"
        )
    return {
        "peers": peers,
        "clean_peers": clean_peers,
        "clean_count": str(len(clean)),
        "dead_count": str(len(dead)),
        "clean_evidence": " | ".join(evidence),
        "best_clean": clean[0] if clean else {},
        "best_dead": dead[0] if dead else {},
    }


def source_summary(rows: list[dict[str, str]]) -> dict[str, str]:
    unique: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for row in rows:
        key = (
            row.get("source", ""),
            row.get("original_url", ""),
            row.get("source_match", ""),
            row.get("source_resource_status", ""),
        )
        unique.setdefault(key, row)
    confirmed = [row for row in unique.values() if row.get("source_match") == "confirmed"]
    details = []
    for row in confirmed:
        title = row.get("source_title", "").replace("\n", " ")[:160]
        details.append(
            f"{row.get('source', '')}:{row.get('source_resource_status', '')} "
            f"id={row.get('source_id', '')} title={title}"
        )
    return {
        "sources": distinct([row.get("source", "") for row in confirmed]),
        "match_count": str(len(confirmed)),
        "matches": " | ".join(details),
    }


def write_flagged(path: Path, rows: list[dict[str, str]]) -> None:
    flagged = [row for row in rows if row.get("final_classification") not in CLEAN_CLASSES]
    fields = [
        "final_classification",
        "final_status",
        "providers",
        "occurrences",
        "url",
        "final_method",
        "final_reason",
        "source_verified",
        "source_match_count",
        "remote_peers",
        "remote_clean_peers",
        "remote_clean_count",
        "remote_evidence",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in flagged)

    html_path = path.with_suffix(".html")
    with html_path.open("w", encoding="utf-8") as handle:
        handle.write("<!doctype html><meta charset=\"utf-8\"><title>Pass 4 URL health follow-ups</title>")
        handle.write("<style>body{font:14px system-ui,sans-serif;margin:2rem}table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccc;padding:.35rem;text-align:left;vertical-align:top}th{position:sticky;top:0;background:#eee}a{overflow-wrap:anywhere}</style>")
        handle.write("<h1>Pass 4 URL health follow-ups</h1><p>Rows below were not confirmed as a normal GET success or redirect by local or remote probes. Source-level confirmations are shown separately and are not treated as HTML GET success.</p><table><thead><tr><th>classification</th><th>status</th><th>url</th><th>source evidence</th><th>remote peers</th><th>remote clean peers</th><th>reason</th></tr></thead><tbody>")
        for row in flagged:
            url = html.escape(row.get("url", ""), quote=True)
            handle.write(
                "<tr>"
                f"<td>{html.escape(row.get('final_classification', ''))}</td>"
                f"<td>{html.escape(row.get('final_status', ''))}</td>"
                f"<td><a href=\"{url}\">{url}</a></td>"
                f"<td>{html.escape(row.get('source_verified', ''))}</td>"
                f"<td>{html.escape(row.get('remote_peers', ''))}</td>"
                f"<td>{html.escape(row.get('remote_clean_peers', ''))}</td>"
                f"<td>{html.escape(row.get('final_reason', ''))}</td>"
                "</tr>"
            )
        handle.write("</tbody></table>")


def write_resolution_summary(path: Path, rows: list[dict[str, str]]) -> Counter[str]:
    order = [
        "confirmed_get_ok",
        "confirmed_get_redirect",
        "likely_dead_not_found",
        "source_confirmed_not_html",
        "access_or_rate_limit_warning",
        "server_or_network_warning",
        "reachable_tls_warning",
        "reachable_alternate_network",
    ]
    resolution_rows = []
    counts: Counter[str] = Counter()
    for row in rows:
        classification = row.get("final_classification", "")
        if classification not in CLEAN_CLASSES | {DEAD_CLASS} and as_int(row.get("source_match_count")) > 0:
            classification = "source_confirmed_not_html"
        counts[classification] += 1
    total = len(rows)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["resolution", "urls", "percent"])
        for classification in order:
            count = counts.get(classification, 0)
            writer.writerow([classification, count, f"{count / total * 100:.2f}"])
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--source", type=Path, action="append", required=True)
    parser.add_argument("--remote-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    rows = read_csv(args.base / "consolidated-results.csv")
    source_by_url: dict[str, list[dict[str, str]]] = defaultdict(list)
    source_file_rows = 0
    for source_path in args.source:
        source_values = read_source(source_path)
        source_file_rows += sum(len(values) for values in source_values.values())
        for url, values in source_values.items():
            source_by_url[url].extend(values)
    remote_by_url: dict[str, list[dict[str, str]]] = defaultdict(list)
    remote_files = sorted(args.remote_dir.glob("*.csv"))
    for path in remote_files:
        for url, values in read_remote(path).items():
            remote_by_url[url].extend(values)

    original_fields = list(rows[0].keys())
    appended = [
        "pass4_prior_classification",
        "pass4_prior_status",
        "pass4_prior_url",
        "pass4_prior_method",
        "pass4_prior_reason",
        "source_verified",
        "source_match_count",
        "source_matches",
        "remote_peers",
        "remote_clean_peers",
        "remote_clean_count",
        "remote_dead_count",
        "remote_evidence",
        "remote_observed_statuses",
    ]

    clean_promotions = 0
    dead_promotions = 0
    source_confirmed_urls = 0
    dual_remote_clean_urls = 0
    all_remote_clean_urls = 0
    for row in rows:
        url = row["url"]
        row["pass4_prior_classification"] = row.get("final_classification", "")
        row["pass4_prior_status"] = row.get("final_status", "")
        row["pass4_prior_url"] = row.get("final_url", "")
        row["pass4_prior_method"] = row.get("final_method", "")
        row["pass4_prior_reason"] = row.get("final_reason", "")

        source = source_summary(source_by_url.get(url, []))
        row["source_verified"] = source["sources"]
        row["source_match_count"] = source["match_count"]
        row["source_matches"] = source["matches"]
        if as_int(source["match_count"]) > 0:
            source_confirmed_urls += 1

        remote = remote_summary(remote_by_url.get(url, []))
        row["remote_peers"] = remote["peers"]
        row["remote_clean_peers"] = remote["clean_peers"]
        row["remote_clean_count"] = remote["clean_count"]
        row["remote_dead_count"] = remote["dead_count"]
        row["remote_evidence"] = remote["clean_evidence"]
        row["remote_observed_statuses"] = distinct([
            f"{item.get('remote_hostname', '')}:{item.get('http_status', '') or 'none'}"
            for item in remote_by_url.get(url, [])
        ])
        clean_count = as_int(remote["clean_count"])
        peer_count = len([peer for peer in remote["peers"].split(";") if peer])
        if clean_count >= 2:
            dual_remote_clean_urls += 1
        if peer_count and clean_count == peer_count:
            all_remote_clean_urls += 1

        if url in remote_by_url and row["pass4_prior_classification"] not in CLEAN_CLASSES | {DEAD_CLASS}:
            best = remote["best_clean"]
            if best:
                status = as_int(best.get("http_status"))
                final_url = best.get("final_url") or url
                row["final_classification"] = "confirmed_get_redirect" if final_url != url or as_int(best.get("redirects")) else "confirmed_get_ok"
                row["final_status"] = str(status)
                row["final_url"] = final_url
                row["final_method"] = f"remote-http:{best.get('remote_hostname', '')}"
                row["final_reason"] = "a Tailscale remote peer returned a normal non-challenge GET with a validated body"
                clean_promotions += 1
            elif remote["best_dead"]:
                best = remote["best_dead"]
                row["final_classification"] = DEAD_CLASS
                row["final_status"] = str(as_int(best.get("http_status")))
                row["final_url"] = best.get("final_url") or url
                row["final_method"] = f"remote-http:{best.get('remote_hostname', '')}"
                row["final_reason"] = "a Tailscale remote peer observed a validated HTTP 404/410"
                dead_promotions += 1

    with (args.out / "consolidated-results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=original_fields + appended)
        writer.writeheader()
        writer.writerows(rows)

    counts = write_summary(args.out, rows)
    resolution_counts = write_resolution_summary(args.out / "resolution-summary.csv", rows)
    write_provider_summary(args.out, rows, counts)
    write_flagged(args.out / "consolidated-flagged.csv", rows)
    write_chart(args.out, counts)
    method = [
        "Pass 4 retained the pass-3 result as the baseline and added public source-level verification for Stack Exchange questions, Medium RSS articles, and DOI metadata from Crossref.",
        "The source verifier ran once locally and once through the macbook-pro Tailscale peer. Their union contained 131 distinct source-confirmed URLs.",
        "Three Tailscale peers ran bounded normal HTTPS GET checks with certificate verification and HTTP/1.1: ubuntu-paris, pi-omv-2026, and macbook-pro.",
        "The remote lane checked 339 residual URLs on each peer and recorded status, final URL, title, body size, redirect count, and curl errors without storing page bodies or cookies.",
        "Only non-challenge remote responses with a real title or substantial body were promoted to GET OK or GET redirect. Access, rate-limit, server, and transport responses stayed warnings.",
        "The remote lane produced 70 unique clean body confirmations. 44 were clean on at least two peers and 4 were clean on all three.",
        "Source confirmations remain separate from HTML GET health: they show that the resource exists in a public API, RSS feed, or DOI registry.",
    ]
    payload = {
        "tool": "linkinator-plus-source-and-tailscale-followups",
        "unique_urls": len(rows),
        "remote_files": [path.name for path in remote_files],
        "source_verification_rows": source_file_rows,
        "source_confirmed_urls": source_confirmed_urls,
        "remote_clean_promotions": clean_promotions,
        "remote_dead_promotions": dead_promotions,
        "dual_remote_clean_urls": dual_remote_clean_urls,
        "all_remote_clean_urls": all_remote_clean_urls,
        "classification_counts": dict(counts),
        "resolution_counts": dict(resolution_counts),
        "method": method,
    }
    (args.out / "consolidated.json").write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
