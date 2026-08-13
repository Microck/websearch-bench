#!/usr/bin/env python3
"""Normalize a Linkinator run into benchmark link-health artifacts.

Linkinator checks each URL as a web link. This script keeps that signal
separate from the benchmark's resolver: 404 and 410 responses are the only
statuses called likely dead, while access blocks, rate limits, redirects, and
network failures remain separate warnings.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from html import escape
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def normalize_url(value: str) -> str:
    """Match Linkinator's URL form without treating fragments as new pages."""
    try:
        parts = urlsplit((value or "").strip())
        path = parts.path.rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))
    except ValueError:
        return (value or "").strip()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, object]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def status_value(result: dict[str, object]) -> int | None:
    value = result.get("status")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def result_priority(result: dict[str, object]) -> tuple[int, int]:
    state = str(result.get("state") or "")
    state_score = {"BROKEN": 3, "OK": 2, "SKIPPED": 1}.get(state, 0)
    status = status_value(result) or 0
    return state_score, status


def failure_text(result: dict[str, object]) -> str:
    failures = result.get("failureDetails") or []
    values: list[str] = []
    for failure in failures:
        if isinstance(failure, dict):
            status = failure.get("status")
            target = failure.get("url")
            if status is not None or target:
                values.append(" ".join(part for part in [f"status={status}" if status is not None else "", f"url={target}" if target else ""] if part))
            else:
                values.append(json.dumps(failure, sort_keys=True))
        else:
            values.append(str(failure))
    return " | ".join(values)


def classify(result: dict[str, object], has_redirect: bool) -> str:
    state = str(result.get("state") or "")
    status = status_value(result)
    if state == "SKIPPED":
        return "unresolved_skipped"
    if status in {404, 410} and state == "BROKEN":
        return "likely_dead_not_found"
    if status in {401, 403, 429}:
        return "access_or_rate_limit_warning"
    if status is None or status == 0 or status >= 500:
        return "server_or_network_warning"
    if state == "BROKEN":
        return "other_broken"
    if has_redirect or (status is not None and 300 <= status < 400):
        return "ok_redirect"
    return "ok"


def load_linkinator(batch_dir: Path) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    results: dict[str, list[dict[str, object]]] = defaultdict(list)
    redirects: list[dict[str, object]] = []
    metadata: list[dict[str, object]] = []
    for path in sorted(batch_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        batch_index = payload.get("batchIndex")
        metadata.append({
            "batch": batch_index,
            "requested": len(payload.get("requested") or []),
            "root_results": len((payload.get("result") or {}).get("links") or []),
            "discovered_links": (payload.get("result") or {}).get("discoveredLinkCount", 0),
            "uncaught": payload.get("uncaught", 0),
            "passed": (payload.get("result") or {}).get("passed"),
        })
        result_block = payload.get("result") or {}
        for result in result_block.get("links") or []:
            if isinstance(result, dict):
                result = dict(result)
                result["batch"] = batch_index
                results[normalize_url(str(result.get("url") or ""))].append(result)
        for redirect in result_block.get("redirects") or []:
            if isinstance(redirect, dict):
                redirect = dict(redirect)
                redirect["batch"] = batch_index
                redirects.append(redirect)
    selected = [max(values, key=result_priority) for values in results.values()]
    return selected, redirects, metadata


def build_rows(run_dir: Path, linkinator_results: list[dict[str, object]], redirects: list[dict[str, object]]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    result_rows = read_csv(run_dir / "retrieved_results.csv")
    by_url: dict[str, dict[str, object]] = defaultdict(lambda: {"providers": set(), "occurrences": 0})
    for row in result_rows:
        key = normalize_url(row.get("url", ""))
        if not key:
            continue
        entry = by_url[key]
        entry["providers"].add(row.get("provider", ""))
        entry["occurrences"] += 1

    redirect_map: dict[str, list[dict[str, object]]] = defaultdict(list)
    for redirect in redirects:
        redirect_map[normalize_url(str(redirect.get("url") or ""))].append(redirect)
    checked = {normalize_url(str(row.get("url") or "")): row for row in linkinator_results}
    rows: list[dict[str, object]] = []
    for key, entry in sorted(by_url.items()):
        result = checked.get(key)
        if result is None:
            rows.append({
                "url": key,
                "providers": ";".join(sorted(entry["providers"])),
                "occurrences": entry["occurrences"],
                "checked": "no",
                "checked_url": "",
                "status": "",
                "state": "",
                "classification": "unresolved_missing",
                "redirect_target": "",
                "failure": "No Linkinator root result",
            })
            continue
        redirects_for_url = redirect_map.get(key, [])
        targets = sorted({str(item.get("targetUrl")) for item in redirects_for_url if item.get("targetUrl")})
        rows.append({
            "url": key,
            "providers": ";".join(sorted(entry["providers"])),
            "occurrences": entry["occurrences"],
            "checked": "yes",
            "checked_url": str(result.get("url") or ""),
            "status": status_value(result) if status_value(result) is not None else "",
            "state": result.get("state", ""),
            "classification": classify(result, bool(redirects_for_url)),
            "redirect_target": ";".join(targets),
            "failure": failure_text(result),
        })
    return rows, result_rows


def provider_summary(link_rows: list[dict[str, object]], result_rows: list[dict[str, str]]) -> list[dict[str, object]]:
    providers = sorted({row.get("provider", "") for row in result_rows})
    output: list[dict[str, object]] = []
    for provider in providers:
        urls = {normalize_url(row.get("url", "")) for row in result_rows if row.get("provider") == provider}
        rows = [row for row in link_rows if provider in str(row.get("providers", "")).split(";")]
        counts = Counter(row.get("classification") for row in rows)
        checked = sum(1 for row in rows if row.get("checked") == "yes")
        output.append({
            "provider": provider,
            "result_rows": sum(1 for row in result_rows if row.get("provider") == provider),
            "unique_urls": len(urls),
            "checked_urls": checked,
            "coverage_pct": round(100 * checked / len(urls), 2) if urls else 0,
            "ok": counts["ok"],
            "ok_redirect": counts["ok_redirect"],
            "likely_dead_not_found": counts["likely_dead_not_found"],
            "access_or_rate_limit_warning": counts["access_or_rate_limit_warning"],
            "server_or_network_warning": counts["server_or_network_warning"],
            "other_broken": counts["other_broken"],
            "unresolved": counts["unresolved_missing"] + counts["unresolved_skipped"],
        })
    return output


def write_flagged_html(path: Path, rows: list[dict[str, object]], only_likely_dead: bool = False) -> None:
    if only_likely_dead:
        flagged = [row for row in rows if row["classification"] == "likely_dead_not_found"]
        heading = "Linkinator likely dead result URLs"
        intro = "Only HTTP 404 and 410 responses are listed. These are likely dead, but a later manual check can still find a replacement or an access-dependent page."
    else:
        flagged = [row for row in rows if row["classification"] not in {"ok"}]
        heading = "Linkinator flagged result URLs"
        intro = "Only URLs not classified as plain <code>ok</code> are listed. A 404/410 is a likely dead link. Access blocks, rate limits, redirects, and network failures are separate warnings."
    body = []
    for row in flagged:
        body.append(
            "<tr>"
            f"<td>{escape(str(row['classification']))}</td>"
            f"<td>{escape(str(row['status']))}</td>"
            f"<td>{escape(str(row['providers']))}</td>"
            f"<td>{escape(str(row['occurrences']))}</td>"
            f"<td><a href=\"{escape(str(row['url']), quote=True)}\">{escape(str(row['url']))}</a></td>"
            f"<td>{escape(str(row['redirect_target']))}</td>"
            f"<td>{escape(str(row['failure']))}</td>"
            "</tr>"
        )
    heading_html = escape(heading)
    html = """<!doctype html>
<meta charset="utf-8">
<title>""" + heading_html + """</title>
<style>body{font:14px system-ui,sans-serif;margin:2rem}table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccc;padding:.35rem;text-align:left;vertical-align:top}th{position:sticky;top:0;background:#eee}a{overflow-wrap:anywhere}</style>
<h1>""" + heading_html + """</h1>
<p>""" + intro + """</p>
<table><thead><tr><th>classification</th><th>status</th><th>providers</th><th>occurrences</th><th>url</th><th>redirect target</th><th>failure</th></tr></thead><tbody>
""" + "\n".join(body) + "</tbody></table>\n"
    path.write_text(html, encoding="utf-8")


def write_health_chart(path: Path, summary: list[dict[str, object]]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = [str(row["provider"]) for row in summary]
    display = {
        "brave": "Brave",
        "context": "Context.dev",
        "exa": "Exa",
        "kagi": "Kagi",
        "octen": "Octen",
        "parallel": "Parallel.ai",
        "perplexity": "Perplexity",
        "tavily": "Tavily",
    }
    categories = [
        ("ok", "OK", "#4c78a8"),
        ("ok_redirect", "OK, redirected", "#72b7b2"),
        ("access_or_rate_limit_warning", "Access/rate limit", "#f2cf5b"),
        ("server_or_network_warning", "Server/network", "#f58518"),
        ("likely_dead_not_found", "Likely dead (404/410)", "#e45756"),
        ("other_broken", "Other broken", "#b279a2"),
        ("unresolved", "Unresolved", "#9d9d9d"),
    ]
    fig, ax = plt.subplots(figsize=(10, 5.4))
    bottom = [0] * len(summary)
    for key, label, color in categories:
        values = [int(row.get(key, 0)) for row in summary]
        ax.bar([display.get(label, label) for label in labels], values, bottom=bottom, label=label, color=color)
        bottom = [current + value for current, value in zip(bottom, values)]
    ax.set_ylabel("Unique result URLs")
    ax.set_title("Linkinator health by provider")
    ax.grid(axis="y", alpha=0.2)
    ax.legend(ncol=2, frameon=False, loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    linkinator_results, redirects, metadata = load_linkinator(args.batch_dir)
    rows, result_rows = build_rows(args.run_dir, linkinator_results, redirects)
    summary = provider_summary(rows, result_rows)
    columns = ["url", "providers", "occurrences", "checked", "checked_url", "status", "state", "classification", "redirect_target", "failure"]
    write_csv(args.out_dir / "linkinator-results.csv", rows, columns)
    write_csv(args.out_dir / "linkinator-flagged.csv", [row for row in rows if row["classification"] != "ok"], columns)
    write_csv(args.out_dir / "linkinator-dead.csv", [row for row in rows if row["classification"] == "likely_dead_not_found"], columns)
    write_csv(args.out_dir / "linkinator-provider-summary.csv", summary, list(summary[0].keys()) if summary else ["provider"])
    write_csv(args.out_dir / "linkinator-batches.csv", metadata, list(metadata[0].keys()) if metadata else ["batch"])
    (args.out_dir / "linkinator-results.json").write_text(json.dumps({
        "tool": "linkinator",
        "raw_result_url_count": len({row.get("url", "") for row in result_rows if row.get("url", "")}),
        "root_url_count": len(rows),
        "redirect_event_count": len(redirects),
        "results": rows,
    }, indent=2), encoding="utf-8")
    write_flagged_html(args.out_dir / "linkinator-flagged.html", rows)
    write_flagged_html(args.out_dir / "linkinator-dead.html", rows, only_likely_dead=True)
    write_health_chart(args.out_dir / "linkinator-health.png", summary)
    print(json.dumps({
        "raw_urls": len({row.get("url", "") for row in result_rows if row.get("url", "")} ),
        "urls": len(rows),
        "flagged": sum(row["classification"] != "ok" for row in rows),
        "likely_dead": sum(row["classification"] == "likely_dead_not_found" for row in rows),
        "redirect_events": len(redirects),
    }))


if __name__ == "__main__":
    main()
