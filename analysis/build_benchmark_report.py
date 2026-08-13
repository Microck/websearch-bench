#!/usr/bin/env python3
"""Build a standalone HTML report from an Octen anchor benchmark run."""

from __future__ import annotations

import argparse
import html
import json
import sys
import textwrap
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

# Make the script work when invoked directly from the repository root, which
# is the same launch shape used by the existing analysis entry points.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from runners.run_benchmark import canonicalize_url

try:
    from analysis.chart_style import DISPLAY_NAMES
except ImportError:  # pragma: no cover - supports direct analysis imports
    from chart_style import DISPLAY_NAMES


BRAND_COLORS = {
    "brave": "#4f30ab",
    "context": "#5e6ad2",
    "exa": "#1840ed",
    "kagi": "#c78a00",
    "octen": "#238b3d",
    "parallel": "#d84e13",
    "perplexity": "#168da0",
    "tavily": "#4f806f",
}
CATEGORY_LABELS = {
    "academic": "Academic",
    "finance": "Finance",
    "general": "General",
    "local": "Local",
    "medical_legal": "Medical and legal",
    "news": "News",
    "tech_code": "Tech and code",
    "entertainment": "Entertainment",
    "ecommerce": "Ecommerce",
}


def provider_label(provider: str) -> str:
    return DISPLAY_NAMES.get(provider, provider)


def join_labels(labels: list[str]) -> str:
    if len(labels) < 2:
        return labels[0] if labels else "No provider"
    if len(labels) == 2:
        return " and ".join(labels)
    return ", ".join(labels[:-1]) + f", and {labels[-1]}"


def numeric_sum(frame: pd.DataFrame, provider: str, column: str) -> float | None:
    if column not in frame.columns:
        return None
    values = pd.to_numeric(frame.loc[frame["provider"] == provider, column], errors="coerce").dropna()
    if values.empty:
        return None
    return float(values.sum())


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def match_gold(result_url: str, anchor: dict[str, Any]) -> bool:
    gold = anchor.get("gold") or {}
    gold_urls = [canonicalize_url(str(url)) for url in gold.get("gold_urls", [])]
    normalized = canonicalize_url(result_url)
    if not normalized or not gold_urls:
        return False
    if gold.get("url_match") == "prefix":
        return any(normalized.startswith(gold_url) for gold_url in gold_urls)
    return normalized in gold_urls


def build_query_outcomes(
    anchors: list[dict[str, Any]],
    providers: list[str],
    results: pd.DataFrame,
) -> list[dict[str, Any]]:
    result_map: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in results.to_dict("records"):
        result_map[(str(row["provider"]), str(row["query_id"]))].append(row)
    for rows in result_map.values():
        rows.sort(key=lambda row: int(row.get("rank") or 0))

    outcomes: list[dict[str, Any]] = []
    for anchor in anchors:
        qid = str(anchor["qid"])
        provider_outcomes: dict[str, dict[str, Any]] = {}
        graded = bool((anchor.get("gold") or {}).get("gold_urls"))
        for provider in providers:
            provider_results = result_map[(provider, qid)]
            matching_ranks = [
                int(row["rank"])
                for row in provider_results
                if match_gold(str(row.get("canonical_url") or row.get("url") or ""), anchor)
            ]
            first_rank = min(matching_ranks) if matching_ranks else None
            provider_outcomes[provider] = {
                "graded": graded,
                "first_gold_rank": first_rank,
                "hit_at_1": first_rank == 1,
                "hit_at_10": first_rank is not None and first_rank <= 10,
                "result_count": len(provider_results),
            }
        category = str(anchor.get("vertical") or "other")
        outcomes.append(
            {
                "qid": qid,
                "query": str(anchor.get("query") or ""),
                "category": category,
                "category_label": CATEGORY_LABELS.get(category, category.replace("_", " ").title()),
                "graded": graded,
                "providers": provider_outcomes,
            }
        )
    return outcomes


def build_category_metrics(outcomes: list[dict[str, Any]], providers: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    categories = sorted({str(row["category"]) for row in outcomes})
    for provider in providers:
        for category in categories:
            subset = [row for row in outcomes if row["graded"] and row["category"] == category]
            if not subset:
                continue
            provider_rows = [row["providers"][provider] for row in subset]
            rows.append(
                {
                    "provider": provider,
                    "category": category,
                    "category_label": subset[0]["category_label"],
                    "queries": len(provider_rows),
                    "hit_at_1": round(100 * sum(row["hit_at_1"] for row in provider_rows) / len(provider_rows), 1),
                    "hit_at_10": round(100 * sum(row["hit_at_10"] for row in provider_rows) / len(provider_rows), 1),
                }
            )
    return rows


def build_provider_metrics(summary: pd.DataFrame) -> list[dict[str, Any]]:
    rows = summary.to_dict("records")
    metrics = []
    for row in rows:
        provider = str(row["provider"])
        hit1 = float(row["hit_at_1_rate"]) * 100
        hit10 = float(row["hit_at_10_rate"]) * 100
        queries = int(row["queries_attempted"])
        metrics.append(
            {
                "provider": provider,
                "label": provider_label(provider),
                "color": BRAND_COLORS.get(provider, "#63727b"),
                "queries": queries,
                "graded_queries": int(row["graded_queries"]),
                "successful_attempts": int(row["successful_attempts"]),
                "failed_attempts": int(row["failed_attempts"]),
                "result_rows": int(row["result_rows"]),
                "mean_results": round(int(row["result_rows"]) / queries, 2) if queries else 0,
                "hit_at_1": round(hit1, 1),
                "hit_at_10": round(hit10, 1),
                "lift": round(hit10 - hit1, 1),
            }
        )
    return sorted(metrics, key=lambda row: (-row["hit_at_10"], -row["hit_at_1"], row["label"]))


def chart_x(value: float, start: float, end: float, maximum: float) -> float:
    return start + (end - start) * value / maximum


def hit_rate_svg(metrics: list[dict[str, Any]], graded_queries: int) -> str:
    width = 1120
    left, right, top, row_height = 170, 1050, 92, 54
    height = top + row_height * len(metrics) + 72
    parts = [
        f'<svg class="plot plot-hit-rate" viewBox="0 0 {width} {height}" role="img" aria-labelledby="hit-rate-title hit-rate-desc">',
        '<title id="hit-rate-title">Gold URL hit rate at rank 1 and rank 10 by provider</title>',
        f'<desc id="hit-rate-desc">A dumbbell plot comparing each provider across {graded_queries} gradable anchors. The horizontal scale runs from 0 to 100 percent.</desc>',
    ]
    for tick in range(0, 101, 20):
        x = chart_x(tick, left, right, 100)
        parts.append(f'<line class="plot-grid" x1="{x:.1f}" y1="62" x2="{x:.1f}" y2="{height - 48}"/>')
        parts.append(f'<text class="plot-tick" x="{x:.1f}" y="{height - 24}" text-anchor="middle">{tick}%</text>')
    parts.extend(
        [
            '<circle class="plot-rank-one" cx="742" cy="28" r="5"/>',
            '<text class="plot-legend" x="756" y="33">Gold at rank 1</text>',
            '<circle class="plot-top-ten" cx="888" cy="28" r="7"/>',
            '<text class="plot-legend" x="902" y="33">Gold in top 10</text>',
        ]
    )
    for index, row in enumerate(metrics):
        y = top + index * row_height
        hit_one_x = chart_x(float(row["hit_at_1"]), left, right, 100)
        hit_ten_x = chart_x(float(row["hit_at_10"]), left, right, 100)
        label = html.escape(str(row["label"]))
        parts.extend(
            [
                f'<text class="plot-provider" x="{left - 18}" y="{y + 5}" text-anchor="end">{label}</text>',
                f'<line class="plot-range" x1="{hit_one_x:.1f}" y1="{y}" x2="{hit_ten_x:.1f}" y2="{y}"/>',
                f'<circle class="plot-top-ten" cx="{hit_ten_x:.1f}" cy="{y}" r="7"/>',
                f'<circle class="plot-rank-one" cx="{hit_one_x:.1f}" cy="{y}" r="5"/>',
                f'<text class="plot-value" x="{hit_one_x - 11:.1f}" y="{y - 10}" text-anchor="end">{row["hit_at_1"]:.1f}%</text>',
                f'<text class="plot-value" x="{hit_ten_x + 11:.1f}" y="{y + 18}" text-anchor="start">{row["hit_at_10"]:.1f}%</text>',
            ]
        )
    parts.append("</svg>")
    return "".join(parts)


def heatmap_color(value: float | None) -> tuple[str, str]:
    if value is None:
        return "#e8e4da", "#66727a"
    if value >= 75:
        return "#28764a", "#ffffff"
    if value >= 50:
        return "#72b889", "#102331"
    if value >= 25:
        return "#badcc3", "#102331"
    return "#e7eee5", "#102331"


def category_heatmap_svg(category_rows: list[dict[str, Any]], metrics: list[dict[str, Any]]) -> str:
    categories = list(dict.fromkeys(str(row["category"]) for row in category_rows))
    values = {(str(row["provider"]), str(row["category"])): float(row["hit_at_10"]) for row in category_rows}
    width = 1120
    left, right, top, row_height = 170, 28, 86, 48
    plot_width = width - left - right
    cell_width = plot_width / max(len(categories), 1)
    height = top + row_height * len(metrics) + 68
    parts = [
        f'<svg class="plot plot-heatmap" viewBox="0 0 {width} {height}" role="img" aria-labelledby="category-title category-desc">',
        '<title id="category-title">Top-ten hit rate by provider and query category</title>',
        '<desc id="category-desc">A heatmap where darker green means a higher percentage of gold URLs found in the first ten results.</desc>',
    ]
    for column, category in enumerate(categories):
        x = left + column * cell_width + cell_width / 2
        label = html.escape(CATEGORY_LABELS.get(category, category.replace("_", " ").title()))
        parts.append(f'<text class="plot-column" x="{x:.1f}" y="48" text-anchor="middle">{label}</text>')
    for row_index, metric in enumerate(metrics):
        y = top + row_index * row_height
        parts.append(
            f'<text class="plot-provider" x="{left - 18}" y="{y + row_height / 2 + 5:.1f}" text-anchor="end">{html.escape(str(metric["label"]))}</text>'
        )
        for column, category in enumerate(categories):
            value = values.get((str(metric["provider"]), category))
            fill, text_color = heatmap_color(value)
            x = left + column * cell_width
            label = "-" if value is None else f"{value:.0f}%"
            parts.extend(
                [
                    f'<rect class="heat-cell" x="{x + 1:.1f}" y="{y + 1:.1f}" width="{cell_width - 2:.1f}" height="{row_height - 2}" fill="{fill}"/>',
                    f'<text class="heat-value" x="{x + cell_width / 2:.1f}" y="{y + row_height / 2 + 5:.1f}" text-anchor="middle" fill="{text_color}">{label}</text>',
                ]
            )
    legend_y = height - 27
    for index, (label, fill) in enumerate((("0-24%", "#e7eee5"), ("25-49%", "#badcc3"), ("50-74%", "#72b889"), ("75-100%", "#28764a"))):
        x = left + index * 138
        parts.append(f'<rect x="{x}" y="{legend_y - 13}" width="22" height="14" fill="{fill}"/>')
        parts.append(f'<text class="plot-legend" x="{x + 30}" y="{legend_y}">{label}</text>')
    parts.append("</svg>")
    return "".join(parts)


def result_depth_svg(metrics: list[dict[str, Any]]) -> str:
    width = 1120
    left, right, top, row_height = 170, 86, 64, 50
    height = top + row_height * len(metrics) + 70
    parts = [
        f'<svg class="plot plot-depth" viewBox="0 0 {width} {height}" role="img" aria-labelledby="depth-title depth-desc">',
        '<title id="depth-title">Mean result count by provider</title>',
        '<desc id="depth-desc">A horizontal bar chart on a zero to ten scale. Ten results were requested from every provider.</desc>',
    ]
    for tick in range(0, 11, 2):
        x = chart_x(tick, left, width - right, 10)
        parts.append(f'<line class="plot-grid" x1="{x:.1f}" y1="42" x2="{x:.1f}" y2="{height - 48}"/>')
        parts.append(f'<text class="plot-tick" x="{x:.1f}" y="{height - 23}" text-anchor="middle">{tick}</text>')
    for index, row in enumerate(metrics):
        y = top + index * row_height
        bar_width = chart_x(float(row["mean_results"]), left, width - right, 10) - left
        parts.extend(
            [
                f'<text class="plot-provider" x="{left - 18}" y="{y + 20}" text-anchor="end">{html.escape(str(row["label"]))}</text>',
                f'<rect class="depth-track" x="{left}" y="{y}" width="{width - right - left}" height="26"/>',
                f'<rect class="depth-bar" x="{left}" y="{y}" width="{bar_width:.1f}" height="26" fill="{html.escape(str(row["color"]))}"/>',
                f'<text class="plot-value depth-value" x="{left + bar_width + 10:.1f}" y="{y + 19}" text-anchor="start">{row["mean_results"]:.2f}</text>',
            ]
        )
    parts.append("</svg>")
    return "".join(parts)


def build_page_data(
    run_dir: Path,
    manifest: dict[str, Any],
    summary_report: dict[str, Any],
    usage: pd.DataFrame,
    metrics: list[dict[str, Any]],
    categories: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
) -> dict[str, Any]:
    context_credits = numeric_sum(usage, "context", "credits_consumed")
    exa_cost = numeric_sum(usage, "exa", "cost_usd_reported")
    return {
        "run": {
            "name": run_dir.name,
            "created_at": str(manifest.get("created_at", "")).split("T", 1)[0],
            "queries_attempted": int(summary_report["queries_attempted"]),
            "graded_queries": int(summary_report["graded_queries"]),
            "ungraded_queries": int(summary_report["ungraded_queries"]),
            "ungraded_query_ids": summary_report.get("ungraded_query_ids", []),
            "provider_count": len(metrics),
            "direct_calls": sum(row["queries"] for row in metrics),
            "failed_attempts": sum(row["failed_attempts"] for row in metrics),
        },
        "providers": metrics,
        "categories": categories,
        "queries": outcomes,
        "usage": {
            "context_credits": context_credits,
            "exa_cost_usd": exa_cost,
            "context_key_request_split": summary_report.get("context_key_request_split", {}),
        },
    }


def json_for_script(value: Any) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def provider_summary_rows(metrics: list[dict[str, Any]]) -> str:
    rows = []
    for row in metrics:
        rows.append(
            "<tr>"
            f"<th scope=\"row\"><span class=\"provider-dot\" style=\"--provider-color:{row['color']}\"></span>{html.escape(row['label'])}</th>"
            f"<td>{row['hit_at_1']:.1f}%</td>"
            f"<td>{row['hit_at_10']:.1f}%</td>"
            f"<td>{row['mean_results']:.2f}</td>"
            f"<td>{row['successful_attempts']}/{row['queries']}</td>"
            "</tr>"
        )
    return "".join(rows)


def provider_headers(metrics: list[dict[str, Any]]) -> str:
    return "".join(f"<th scope=\"col\">{html.escape(row['label'])}</th>" for row in metrics)


def build_html(page_data: dict[str, Any]) -> str:
    metrics = page_data["providers"]
    run = page_data["run"]
    usage = page_data["usage"]
    best_first = max(metrics, key=lambda row: (row["hit_at_1"], row["hit_at_10"]))
    best_depth = max(metrics, key=lambda row: (row["hit_at_10"], row["hit_at_1"]))
    largest_lift = max(metrics, key=lambda row: row["lift"])
    depth_leaders = join_labels([row["label"] for row in metrics if row["hit_at_10"] == best_depth["hit_at_10"]])
    unreported_usage_labels = [
        row["label"] for row in metrics if row["provider"] not in {"context", "exa"}
    ]
    unreported_usage = join_labels(unreported_usage_labels)
    context_credits = usage["context_credits"]
    exa_cost = usage["exa_cost_usd"]
    context_credit_text = "n/a" if context_credits is None else f"{context_credits:.0f} credits"
    exa_cost_text = "not reported" if exa_cost is None else f"${exa_cost:.2f} reported"
    categories = sorted({row["category"] for row in page_data["queries"]})
    category_options = "".join(
        f"<option value=\"{html.escape(category)}\">{html.escape(CATEGORY_LABELS.get(category, category))}</option>"
        for category in categories
    )
    ungraded_ids = ", ".join(run["ungraded_query_ids"])
    data_json = json_for_script(page_data)
    hit_rate_chart = hit_rate_svg(metrics, run["graded_queries"])
    category_chart = category_heatmap_svg(page_data["categories"], metrics)
    depth_chart = result_depth_svg(metrics)

    document = textwrap.dedent(
        f"""\
        <!doctype html>
        <html lang="en">
        <head>
          <meta charset="utf-8">
          <meta name="viewport" content="width=device-width, initial-scale=1">
          <meta name="description" content="Interactive Octen web search benchmark report comparing eight providers on 60 navigational anchor queries.">
          <title>Octen anchor benchmark | web search</title>
          <style>
            :root {{
              color-scheme: light;
              --paper: #f6f3ea;
              --card: #fffdf8;
              --ink: #102331;
              --muted: #66727a;
              --line: #d9d3c5;
              --soft: #ebe7dc;
              --coral: #ad402e;
            }}
            * {{ box-sizing: border-box; }}
            html {{ scroll-behavior: smooth; }}
            body {{
              margin: 0;
              background: var(--paper);
              color: var(--ink);
              font: 16px/1.55 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
              text-rendering: optimizeLegibility;
            }}
            a {{ color: inherit; }}
            .skip-link {{ position: absolute; left: 1rem; top: -5rem; z-index: 5; padding: .75rem 1rem; background: var(--ink); color: white; border-radius: 8px; }}
            .skip-link:focus {{ top: 1rem; }}
            :focus-visible {{ outline: 3px solid currentColor; outline-offset: 3px; }}
            .sr-only {{ position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }}
            .shell {{ width: min(1200px, calc(100% - 2rem)); margin: 0 auto; }}
            .hero {{ background: var(--ink); color: #f8f5ed; padding: 1.1rem 0 4.5rem; }}
            .topbar {{ display: flex; align-items: center; justify-content: space-between; gap: 1rem; max-width: 1200px; margin: 0 auto; padding: 0 1rem .8rem; border-bottom: 1px solid #394954; }}
            .brand {{ font-weight: 760; letter-spacing: -.02em; text-decoration: none; }}
            .nav {{ display: flex; flex-wrap: wrap; gap: .9rem; font-size: .84rem; color: #c3ccd0; }}
            .nav a {{ padding: .55rem .2rem; text-decoration: underline; text-decoration-color: transparent; text-underline-offset: .25rem; }}
            .nav a:hover, .nav a:focus-visible {{ color: white; text-decoration-color: currentColor; }}
            .hero-grid {{ display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(300px, .75fr); gap: 4rem; align-items: start; padding: 4.4rem 0 0; }}
            .hero-meta {{ margin: 0 0 1rem; color: #ff927a; font-size: .78rem; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; }}
            .hero-meta span {{ color: #8fa0a8; }}
            h1, h2, h3, p {{ margin-top: 0; }}
            h1 {{ max-width: 760px; margin-bottom: 1.2rem; font-size: clamp(3rem, 6.2vw, 5.6rem); line-height: .96; letter-spacing: -.065em; font-weight: 820; }}
            .dek {{ max-width: 650px; color: #c8d0d2; font-size: clamp(1.05rem, 1.7vw, 1.3rem); }}
            .hero-note {{ max-width: 650px; margin: 2rem 0 0; border-top: 1px solid #394954; padding-top: 1rem; color: #c8d0d2; font-size: .92rem; }}
            .hero-note strong {{ color: white; }}
            .hero-ledger {{ margin: 0; border-top: 1px solid #3b4b55; }}
            .hero-ledger div {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 1.5rem; align-items: baseline; padding: .9rem 0; border-bottom: 1px solid #3b4b55; }}
            .hero-ledger dt {{ color: #b6c2c5; font-size: .86rem; }}
            .hero-ledger dd {{ margin: 0; font-size: 1.45rem; font-weight: 800; line-height: 1; letter-spacing: -.04em; font-variant-numeric: tabular-nums; }}
            main {{ padding-bottom: 5rem; }}
            .section {{ margin-top: 4.6rem; scroll-margin-top: 1.25rem; }}
            .section-heading {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(280px, .62fr); gap: 2rem; align-items: start; margin-bottom: 1.5rem; border-top: 1px solid var(--line); padding-top: .9rem; }}
            .section-heading h2 {{ max-width: 700px; margin-bottom: 0; font-size: clamp(1.9rem, 3.2vw, 2.8rem); line-height: 1.02; letter-spacing: -.05em; }}
            .section-heading p {{ max-width: 520px; margin: .2rem 0 0; color: var(--muted); }}
            .section-heading-stack {{ display: block; }}
            .section-heading-stack p {{ max-width: 70ch; margin-top: .75rem; }}
            .section-heading-solo {{ display: block; margin-bottom: 1.1rem; }}
            .findings {{ display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(320px, .65fr); border-top: 2px solid var(--ink); border-bottom: 1px solid var(--line); }}
            .primary-finding {{ padding: 1.1rem 2rem 1.5rem 0; border-right: 1px solid var(--line); }}
            .insight-label {{ margin: 0 0 .85rem; color: var(--coral); font-size: .72rem; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; }}
            .primary-finding h3 {{ margin-bottom: .8rem; font-size: clamp(1.7rem, 3vw, 2.6rem); line-height: 1; letter-spacing: -.055em; }}
            .finding-score {{ display: block; margin-bottom: .35rem; font-size: clamp(4rem, 7vw, 6.2rem); line-height: .82; letter-spacing: -.08em; font-variant-numeric: tabular-nums; }}
            .primary-finding > p:last-child {{ max-width: 44ch; margin-bottom: 0; color: var(--muted); }}
            .supporting-findings {{ display: grid; }}
            .supporting-finding {{ padding: 1.1rem 0 1.2rem 1.5rem; }}
            .supporting-finding + .supporting-finding {{ border-top: 1px solid var(--line); }}
            .supporting-finding h3 {{ margin-bottom: .55rem; font-size: clamp(1.35rem, 2.4vw, 2rem); line-height: 1.05; letter-spacing: -.045em; font-variant-numeric: tabular-nums; }}
            .supporting-finding > p:last-child {{ max-width: 34ch; margin-bottom: 0; color: var(--muted); font-size: .9rem; }}
            .chart-list {{ border-top: 2px solid var(--ink); }}
            .chart-figure {{ margin: 0; padding: 2rem 0 2.4rem; border-bottom: 1px solid var(--line); }}
            .chart-figure + .chart-figure {{ padding-top: 2.5rem; }}
            .plot-scroll {{ overflow-x: auto; border: 1px solid var(--line); background: var(--card); scrollbar-gutter: stable; }}
            .plot {{ display: block; width: 100%; height: auto; min-width: 760px; color: var(--ink); font-family: inherit; }}
            .plot-heatmap {{ min-width: 920px; }}
            .plot-grid {{ stroke: #d9d3c5; stroke-width: 1; }}
            .plot-range {{ stroke: #8ba896; stroke-width: 5; stroke-linecap: round; }}
            .plot-rank-one {{ fill: var(--ink); }}
            .plot-top-ten {{ fill: var(--card); stroke: #3c9a5f; stroke-width: 4; }}
            .plot-provider {{ fill: var(--ink); font-size: 15px; font-weight: 720; }}
            .plot-value {{ fill: var(--ink); font-size: 13px; font-weight: 760; font-variant-numeric: tabular-nums; }}
            .plot-tick, .plot-legend, .plot-column {{ fill: var(--muted); font-size: 13px; }}
            .plot-column {{ font-weight: 680; }}
            .heat-cell {{ shape-rendering: crispEdges; }}
            .heat-value {{ font-size: 14px; font-weight: 780; font-variant-numeric: tabular-nums; }}
            .depth-track {{ fill: #e8e4da; }}
            .depth-bar {{ opacity: .9; }}
            .depth-value {{ fill: var(--ink); }}
            .chart-copy {{ display: grid; grid-template-columns: minmax(220px, .65fr) minmax(0, 1fr); gap: 2rem; padding-top: .9rem; }}
            .chart-copy h3 {{ margin-bottom: 0; font-size: 1.2rem; letter-spacing: -.025em; }}
            .chart-copy p {{ max-width: 72ch; margin-bottom: 0; color: var(--muted); font-size: .92rem; }}
            .metric-table-wrap, .explorer, .usage-table-wrap {{ overflow: auto; border-top: 2px solid var(--ink); border-bottom: 1px solid var(--line); background: var(--card); }}
            table {{ width: 100%; border-collapse: collapse; min-width: 720px; }}
            th, td {{ padding: .85rem 1rem; border-bottom: 1px solid var(--soft); text-align: right; white-space: nowrap; }}
            th:first-child, td:first-child {{ text-align: left; }}
            thead th {{ color: var(--muted); font-size: .75rem; letter-spacing: .08em; text-transform: uppercase; }}
            tbody th {{ font-weight: 700; }}
            tbody tr:hover {{ background: #f1eee5; }}
            tbody tr:last-child th, tbody tr:last-child td {{ border-bottom: 0; }}
            .provider-dot {{ display: inline-block; width: .62rem; height: .62rem; margin-right: .55rem; border-radius: 50%; background: var(--provider-color); vertical-align: .04em; }}
            .controls {{ display: flex; flex-wrap: wrap; gap: .75rem; align-items: end; margin-bottom: 1rem; border-bottom: 1px solid var(--line); padding-bottom: 1rem; }}
            .control {{ display: grid; gap: .28rem; }}
            label {{ color: var(--muted); font-size: .77rem; font-weight: 750; letter-spacing: .04em; text-transform: uppercase; }}
            input, select {{ min-height: 44px; border: 1px solid #bdb8ac; border-radius: 2px; padding: .55rem .7rem; background: var(--card); color: var(--ink); font: inherit; }}
            input {{ min-width: min(360px, 75vw); }}
            .control-count {{ margin: 0 0 .45rem auto; color: var(--muted); font-size: .88rem; }}
            .explorer table {{ min-width: 1120px; }}
            .explorer thead th:not(:first-child) {{ text-align: center; }}
            .explorer td {{ text-align: center; }}
            .query-cell {{ max-width: 340px; overflow: hidden; text-overflow: ellipsis; }}
            .status {{ font-size: .82rem; font-weight: 720; font-variant-numeric: tabular-nums; }}
            .status-first {{ color: #17623a; font-weight: 850; }}
            .status-hit {{ color: #315a78; }}
            .status-miss {{ color: #963e31; font-weight: 760; }}
            .status-ungraded {{ color: #77736b; }}
            .method-list {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 0 1.5rem; border-top: 2px solid var(--ink); }}
            .method-note {{ padding: 1.1rem 0 1.35rem; border-bottom: 1px solid var(--line); }}
            .method-note:nth-child(even) {{ border-left: 1px solid var(--line); padding-left: 1.5rem; }}
            .method-note h3 {{ margin-bottom: .45rem; font-size: 1.1rem; }}
            .method-note p {{ margin-bottom: 0; color: var(--muted); font-size: .92rem; }}
            .usage-table {{ min-width: 760px; }}
            .usage-table th:first-child {{ width: 18%; }}
            .usage-table td:nth-child(2) {{ width: 18%; font-weight: 780; font-variant-numeric: tabular-nums; }}
            .usage-table th:last-child, .usage-table td:last-child {{ text-align: left; white-space: normal; }}
            footer {{ display: flex; justify-content: space-between; gap: 1rem; padding: 2rem 0; border-top: 1px solid var(--line); color: var(--muted); font-size: .82rem; }}
            @media (max-width: 850px) {{
              .hero-grid, .method-list, .findings {{ grid-template-columns: 1fr; }}
              .chart-copy {{ grid-template-columns: 1fr; gap: .35rem; }}
              .primary-finding {{ padding: 1rem 0 1.5rem; border-right: 0; border-bottom: 1px solid var(--line); }}
              .supporting-finding {{ padding-left: 0; }}
              .method-note:nth-child(even) {{ border-left: 0; padding-left: 0; }}
              .section-heading {{ display: block; }}
              .section-heading p {{ margin-top: .7rem; }}
              .hero {{ padding-bottom: 3rem; }}
              .hero-grid {{ padding-top: 3rem; }}
            }}
            @media (max-width: 560px) {{
              .shell {{ width: min(100% - 1rem, 1200px); }}
              .topbar {{ align-items: flex-start; flex-direction: column; }}
              h1 {{ font-size: clamp(2.7rem, 15vw, 4rem); }}
              .hero-grid {{ gap: 2.5rem; }}
              .chart-figure {{ padding: 1.4rem 0 1.8rem; }}
              .plot-scroll {{ margin-right: -.5rem; border-right: 0; }}
              .controls {{ display: block; }}
              .control {{ margin-bottom: .75rem; }}
              input, select {{ width: 100%; }}
              .control-count {{ margin: .4rem 0 0; }}
              .usage-table {{ min-width: 0; }}
              .usage-table thead {{ position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); }}
              .usage-table, .usage-table tbody, .usage-table tr, .usage-table th, .usage-table td {{ display: block; width: 100%; }}
              .usage-table tr {{ padding: .75rem; border-bottom: 1px solid var(--soft); }}
              .usage-table tr:last-child {{ border-bottom: 0; }}
              .usage-table th, .usage-table td, .usage-table th:last-child, .usage-table td:last-child {{ border: 0; padding: .12rem 0; text-align: left; white-space: normal; }}
              .usage-table td:nth-child(2) {{ width: 100%; font-size: 1.05rem; }}
              .usage-table td:last-child {{ color: var(--muted); font-size: .85rem; }}
              footer {{ display: block; }}
            }}
            @media (prefers-reduced-motion: reduce) {{
              html {{ scroll-behavior: auto; }}
              * {{ transition-duration: 0.01ms !important; animation-duration: 0.01ms !important; animation-iteration-count: 1 !important; }}
            }}
          </style>
        </head>
        <body>
          <a class="skip-link" href="#main">Skip to report</a>
          <header class="hero">
            <div class="topbar">
              <a class="brand" href="#main">Octen / anchor benchmark</a>
              <nav class="nav" aria-label="Report sections">
                <a href="#readout">Readout</a>
                <a href="#charts">Charts</a>
                <a href="#explorer">Query explorer</a>
                <a href="#method">Method</a>
              </nav>
            </div>
            <div class="shell hero-grid">
              <div>
                <p class="hero-meta">Run {html.escape(run['created_at'])} <span>/</span> direct retrieval only</p>
                <h1>Which engine finds the right page first?</h1>
                <p class="dek">{run['provider_count']} search providers answered the same {run['queries_attempted']} navigational queries. This report checks whether an explicit gold page appeared at rank 1 or somewhere in the first ten.</p>
                <p class="hero-note"><strong>Read this as retrieval evidence.</strong> It is not a human relevance study, an answer-quality test, or a single overall score.</p>
              </div>
              <dl class="hero-ledger" aria-label="Benchmark scope">
                <div><dt>Queries attempted</dt><dd>{run['queries_attempted']}</dd></div>
                <div><dt>Gradable anchors</dt><dd>{run['graded_queries']}</dd></div>
                <div><dt>Providers compared</dt><dd>{run['provider_count']}</dd></div>
                <div><dt>Direct calls</dt><dd>{run['direct_calls']}</dd></div>
                <div><dt>Request failures</dt><dd>{run['failed_attempts']}</dd></div>
              </dl>
            </div>
          </header>
          <main id="main" class="shell">
            <section id="readout" class="section">
              <div class="section-heading">
                <h2>Top-ten coverage hides the ranking gap</h2>
                <p>{html.escape(best_depth['label'])} and {html.escape(best_first['label'])} lead on different parts of the task. A top-ten hit is useful, but a first-result hit is what makes a search feel immediate.</p>
              </div>
              <div class="findings">
                <article class="primary-finding">
                  <p class="insight-label">Best at rank 1</p>
                  <h3><span class="finding-score">{best_first['hit_at_1']:.1f}%</span>{html.escape(best_first['label'])}</h3>
                  <p>The official target appeared first on the most gradable queries.</p>
                </article>
                <div class="supporting-findings">
                  <article class="supporting-finding">
                    <p class="insight-label">Best within top 10</p>
                    <h3>{html.escape(best_depth['label'])} / {best_depth['hit_at_10']:.1f}%</h3>
                    <p>The target appeared somewhere in the returned ten results.</p>
                  </article>
                  <article class="supporting-finding">
                    <p class="insight-label">Biggest rank gap</p>
                    <h3>{html.escape(largest_lift['label'])} / +{largest_lift['lift']:.1f} pts</h3>
                    <p>This provider often found the right page, but not near the top.</p>
                  </article>
                </div>
              </div>
            </section>

            <section id="charts" class="section">
              <div class="section-heading">
                <h2>Three measures, three different questions</h2>
                <p>Each graph has a fixed scale and direct labels. The charts stay readable without hover and scroll within their own frame on narrow screens.</p>
              </div>
              <div class="chart-list">
                <figure class="chart-figure">
                  <div class="plot-scroll" tabindex="0" aria-label="Scrollable rank placement graph">{hit_rate_chart}</div>
                  <figcaption class="chart-copy"><h3>Rank 1 versus rank 10</h3><p>{html.escape(depth_leaders)} reached {best_depth['hit_at_10']:.1f}% top-ten coverage. {html.escape(largest_lift['label'])} shows how much can be lost when the right page lands deeper in the list.</p></figcaption>
                </figure>
                <figure class="chart-figure">
                  <div class="plot-scroll" tabindex="0" aria-label="Scrollable category heatmap">{category_chart}</div>
                  <figcaption class="chart-copy"><h3>Where provider differences show up</h3><p>Each cell is hit@10 for one query family. Dashes indicate a category with no gradable anchors after the source set was resolved.</p></figcaption>
                </figure>
                <figure class="chart-figure">
                  <div class="plot-scroll" tabindex="0" aria-label="Scrollable result depth graph">{depth_chart}</div>
                  <figcaption class="chart-copy"><h3>List completeness is not relevance</h3><p>Context.dev and Tavily returned shorter lists on some calls. That changes browsing depth and should be reported beside quality, not folded into it.</p></figcaption>
                </figure>
              </div>
            </section>

            <section class="section" aria-labelledby="summary-title">
              <div class="section-heading section-heading-stack"><h2 id="summary-title">Provider scorecard</h2><p>{run['direct_calls']} direct calls covered {run['queries_attempted']} queries across {run['provider_count']} providers. Quality rates use only the {run['graded_queries']} anchors with an explicit gold URL.</p></div>
              <div class="metric-table-wrap">
                <table>
                  <caption class="sr-only">Provider comparison of hit at rank 1, hit at rank 10, returned result depth, and successful attempts.</caption>
                  <thead><tr><th scope="col">Provider</th><th scope="col">Hit@1</th><th scope="col">Hit@10</th><th scope="col">Mean results</th><th scope="col">Successful</th></tr></thead>
                  <tbody>{provider_summary_rows(metrics)}</tbody>
                </table>
              </div>
            </section>

            <section id="explorer" class="section">
              <div class="section-heading section-heading-stack"><h2>Query-level evidence</h2><p>Search the query text or filter by category. A rank label shows the first gold match; "miss" means no gold URL appeared in the top ten.</p></div>
              <div class="controls">
                <div class="control"><label for="query-filter">Search query text</label><input id="query-filter" type="search" autocomplete="off" placeholder="Try: official docs"></div>
                <div class="control"><label for="category-filter">Category</label><select id="category-filter"><option value="">All categories</option>{category_options}</select></div>
                <p id="control-count" class="control-count" role="status" aria-live="polite"></p>
              </div>
              <div class="explorer">
                <table>
                  <caption class="sr-only">Query-level first gold result rank by provider.</caption>
                  <thead><tr><th scope="col">Query</th><th scope="col">Category</th>{provider_headers(metrics)}</tr></thead>
                  <tbody id="query-rows"></tbody>
                </table>
              </div>
            </section>

            <section id="method" class="section">
              <div class="section-heading section-heading-solo"><h2>What this benchmark can support</h2></div>
              <div class="method-list">
                <article class="method-note"><h3>Hit@1</h3><p>Did the first ranked result match one of Octen's gold URL prefixes? This is the strictest proxy for "the search got me there immediately."</p></article>
                <article class="method-note"><h3>Hit@10</h3><p>Did any of the first ten results match a gold URL prefix? This measures retrieval coverage, not whether the provider ranked the page well.</p></article>
                <article class="method-note"><h3>Gold set</h3><p>{run['graded_queries']} of {run['queries_attempted']} anchors were gradable. These {run['ungraded_queries']} candidates had no qualified gold URL: {html.escape(ungraded_ids)}.</p></article>
                <article class="method-note"><h3>Not an agent benchmark</h3><p>Every call used the direct provider API. No agent generated an answer, opened result pages, or judged relevance.</p></article>
              </div>
            </section>

            <section class="section" aria-labelledby="budget-title">
              <div class="section-heading section-heading-stack"><h2 id="budget-title">Reported usage</h2><p>Only fields returned by providers are shown. Missing prices stay missing rather than becoming invented estimates.</p></div>
              <div class="usage-table-wrap">
                <table class="usage-table">
                  <caption class="sr-only">Provider-reported usage and billing fields for this benchmark run.</caption>
                  <thead><tr><th scope="col">Provider</th><th scope="col">Reported usage</th><th scope="col">Note</th></tr></thead>
                  <tbody>
                    <tr><th scope="row">Context.dev</th><td>{html.escape(context_credit_text)}</td><td>Credits reported across the configured keys.</td></tr>
                    <tr><th scope="row">Exa</th><td>{html.escape(exa_cost_text)}</td><td>Cost reported by the provider for this run.</td></tr>
                    <tr><th scope="row">Other {len(unreported_usage_labels)} providers</th><td>Not reported</td><td>No provider billing field from {html.escape(unreported_usage)}.</td></tr>
                  </tbody>
                </table>
              </div>
            </section>
          </main>
          <footer class="shell"><span>Generated from {html.escape(run['name'])} / direct retrieval only</span><a href="#main">Back to top</a></footer>
          <script>window.BENCHMARK_DATA = {data_json};</script>
          <script>
            (() => {{
              const data = window.BENCHMARK_DATA;
              const rows = document.querySelector('#query-rows');
              const queryFilter = document.querySelector('#query-filter');
              const categoryFilter = document.querySelector('#category-filter');
              const count = document.querySelector('#control-count');
              const escapeHtml = (value) => String(value).replace(/[&<>\"']/g, (character) => ({{'&': '&amp;', '<': '&lt;', '>': '&gt;', '\"': '&quot;', "'": '&#39;'}}[character]));
              const statusCell = (outcome, label) => {{
                if (!outcome.graded) return `<span class="status status-ungraded" title="No gold URL for this anchor" aria-label="${{label}}: ungraded">n/a</span>`;
                if (outcome.first_gold_rank === 1) return `<span class="status status-first" title="Gold match at rank 1" aria-label="${{label}}: gold match at rank 1">#1</span>`;
                if (outcome.first_gold_rank !== null) return `<span class="status status-hit" title="First gold match at rank ${{outcome.first_gold_rank}}" aria-label="${{label}}: first gold match at rank ${{outcome.first_gold_rank}}">#${{outcome.first_gold_rank}}</span>`;
                return `<span class="status status-miss" title="No gold match in the top 10" aria-label="${{label}}: no gold match in the top 10">miss</span>`;
              }};
              const render = () => {{
                const needle = queryFilter.value.trim().toLowerCase();
                const category = categoryFilter.value;
                const filtered = data.queries.filter((query) => (!needle || query.query.toLowerCase().includes(needle)) && (!category || query.category === category));
                rows.innerHTML = filtered.map((query) => {{
                  const providerCells = data.providers.map((provider) => `<td>${{statusCell(query.providers[provider.provider], provider.label)}}</td>`).join('');
                  return `<tr><th scope="row" class="query-cell" title="${{escapeHtml(query.query)}}">${{escapeHtml(query.query)}}</th><td>${{escapeHtml(query.category_label)}}</td>${{providerCells}}</tr>`;
                }}).join('');
                count.textContent = `${{filtered.length}} of ${{data.queries.length}} queries shown`;
              }};
              queryFilter.addEventListener('input', render);
              categoryFilter.addEventListener('change', render);
              render();
            }})();
          </script>
        </body>
        </html>
        """
    )
    return document


def build_report(run_dir: Path, output: Path) -> Path:
    summary_report = json.loads((run_dir / "anchor_summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    anchors = load_jsonl(run_dir / "anchor_queries.jsonl")
    usage = pd.read_csv(run_dir / "provider_usage.csv", keep_default_na=False)
    results = pd.read_csv(run_dir / "retrieved_results.csv", keep_default_na=False)
    summary = pd.DataFrame(summary_report["providers"])
    metrics = build_provider_metrics(summary)
    providers = [row["provider"] for row in metrics]
    outcomes = build_query_outcomes(anchors, providers, results)
    category_metrics = build_category_metrics(outcomes, providers)
    page_data = build_page_data(run_dir, manifest, summary_report, usage, metrics, category_metrics, outcomes)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_html(page_data), encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--out", default="docs/benchmark-report.html", type=Path)
    args = parser.parse_args()
    build_report(args.run_dir, args.out)
    print(f"Wrote report to {args.out}")


if __name__ == "__main__":
    main()
