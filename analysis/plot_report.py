#!/usr/bin/env python3
"""
Generate presentation charts for the Web Search API benchmark.

Required inputs:
  scored_results.csv
  runs.csv

scored_results.csv columns:
  query_id, category, provider, repetition, rank, canonical_url,
  relevance_grade, is_official_or_primary, is_independent_source,
  is_correct_language, is_broken, is_spam, constraints_met,
  constraints_total, subtopics, published_at, is_latest_official

runs.csv columns:
  query_id, query_variant, provider, repetition, started_at,
  latency_ms, http_status, error_type, cost_usd, request_id,
  raw_response_path

Usage:
  python plot_report.py \
    --scores scored_results.csv \
    --runs runs.csv \
    --out charts
"""

from __future__ import annotations

import argparse
import csv
import itertools
import math
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import matplotlib.pyplot as plt
import numpy as np

try:
    from chart_style import (
        BRAND_COLORS,
        category_order,
        finish_heatmap,
        FOOTER_SIZE,
        provider_order,
        style_heatmap_axes,
        annotate_heatmap,
    )
except ImportError:  # pragma: no cover - supports package-style imports
    from .chart_style import (
        BRAND_COLORS,
        category_order,
        finish_heatmap,
        FOOTER_SIZE,
        provider_order,
        style_heatmap_axes,
        annotate_heatmap,
    )

TITLE_PREFIX = ""
METHOD_LABEL = "Metadata-heuristic"
DISPLAY_NAMES = {
    "brave": "Brave",
    "context": "Context.dev",
    "exa": "Exa",
    "kagi_session": "Kagi",
    "octen": "Octen",
    "parallel": "Parallel.ai",
    "perplexity": "Perplexity",
    "tavily": "Tavily",
}


def display_name(provider: str) -> str:
    return DISPLAY_NAMES.get(provider, provider)


def chart_title(value: str) -> str:
    return f"{TITLE_PREFIX}{value}"


def method_title(value: str) -> str:
    return chart_title(f"{METHOD_LABEL} {value}")


def read_csv(path: str) -> List[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: object, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def ndcg_at_k(grades: Sequence[float], ideal_grades: Sequence[float], k: int = 10) -> float:
    def dcg(values: Sequence[float]) -> float:
        total = 0.0
        for rank, grade in enumerate(values[:k], start=1):
            total += (2.0 ** grade - 1.0) / math.log2(rank + 1.0)
        return total

    actual = dcg(grades)
    ideal = dcg(sorted(ideal_grades, reverse=True))
    return actual / ideal if ideal > 0 else 0.0


def percentile_ci(values: Sequence[float], samples: int = 5000, seed: int = 7) -> Tuple[float, float]:
    arr = np.asarray(values, dtype=float)
    if len(arr) == 0:
        return (0.0, 0.0)
    if len(arr) == 1:
        return (float(arr[0]), float(arr[0]))
    rng = np.random.default_rng(seed)
    means = np.empty(samples)
    for i in range(samples):
        means[i] = rng.choice(arr, size=len(arr), replace=True).mean()
    return tuple(np.percentile(means, [2.5, 97.5]))


def build_metrics(score_rows: List[dict]):
    by_query_provider = defaultdict(list)
    pooled_grades = defaultdict(dict)
    category_by_query = {}

    for row in score_rows:
        qid = row["query_id"]
        provider = row["provider"]
        category_by_query[qid] = row.get("category", "")
        rank = as_int(row.get("rank"))
        grade = as_float(row.get("relevance_grade"))
        url = row.get("canonical_url") or row.get("url") or f"{provider}:{rank}"

        by_query_provider[(qid, provider)].append((rank, grade, url, row))
        pooled_grades[qid][url] = max(pooled_grades[qid].get(url, 0.0), grade)

    per_query = []
    providers = sorted({provider for _, provider in by_query_provider})
    query_ids = sorted({qid for qid, _ in by_query_provider})

    for qid in query_ids:
        ideal = list(pooled_grades[qid].values())
        for provider in providers:
            ranked = sorted(by_query_provider.get((qid, provider), []), key=lambda x: x[0])
            grades = [item[1] for item in ranked[:10]]
            grades += [0.0] * max(0, 10 - len(grades))
            per_query.append({
                "query_id": qid,
                "category": category_by_query.get(qid, ""),
                "provider": provider,
                "ndcg10": ndcg_at_k(grades, ideal, 10),
                "p5": sum(g >= 2 for g in grades[:5]) / 5.0,
                "success1": 1.0 if grades and grades[0] >= 3 else 0.0,
            })

    return per_query, by_query_provider, providers


def write_summary(out_dir: Path, per_query: List[dict], run_rows: List[dict]) -> Dict[str, dict]:
    grouped = defaultdict(list)
    for row in per_query:
        grouped[row["provider"]].append(row)

    run_grouped = defaultdict(list)
    for row in run_rows:
        run_grouped[row.get("provider", "")].append(row)

    summary = {}
    for provider, rows in grouped.items():
        ndcgs = [r["ndcg10"] for r in rows]
        latencies = [
            as_float(r.get("latency_ms"))
            for r in run_grouped.get(provider, [])
            if 200 <= as_int(r.get("http_status")) < 300 and as_float(r.get("latency_ms")) > 0
        ]
        costs = [as_float(r.get("cost_usd")) for r in run_grouped.get(provider, [])]
        errors = [
            r for r in run_grouped.get(provider, [])
            if not (200 <= as_int(r.get("http_status")) < 300) or (r.get("error_type") or "").strip()
        ]
        ci_low, ci_high = percentile_ci(ndcgs)
        summary[provider] = {
            "provider": provider,
            "queries": len(rows),
            "mean_ndcg10": float(np.mean(ndcgs)) if ndcgs else 0.0,
            "ci_low": ci_low,
            "ci_high": ci_high,
            "mean_p5": float(np.mean([r["p5"] for r in rows])) if rows else 0.0,
            "success1": float(np.mean([r["success1"] for r in rows])) if rows else 0.0,
            "p50_latency_ms": float(np.percentile(latencies, 50)) if latencies else 0.0,
            "p95_latency_ms": float(np.percentile(latencies, 95)) if latencies else 0.0,
            "mean_cost_usd": float(np.mean(costs)) if costs else 0.0,
            "error_rate": len(errors) / len(run_grouped.get(provider, [])) if run_grouped.get(provider) else 0.0,
        }

    with (out_dir / "summary_metrics.csv").open("w", newline="", encoding="utf-8") as f:
        fields = list(next(iter(summary.values())).keys()) if summary else ["provider"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary.values())

    with (out_dir / "per_query_metrics.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["query_id", "category", "provider", "ndcg10", "p5", "success1"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(per_query)

    return summary


def save_overall_quality(out_dir: Path, summary: Dict[str, dict]) -> None:
    providers = provider_order(summary)
    means = [summary[p]["mean_ndcg10"] for p in providers]
    low = [means[i] - summary[p]["ci_low"] for i, p in enumerate(providers)]
    high = [summary[p]["ci_high"] - means[i] for i, p in enumerate(providers)]

    fig, ax = plt.subplots(figsize=(9, max(4, len(providers) * 0.8)))
    y = np.arange(len(providers))
    for index, provider in enumerate(providers):
        ax.errorbar(
            means[index],
            y[index],
            xerr=[[low[index]], [high[index]]],
            fmt="o",
            capsize=4,
            color=BRAND_COLORS.get(provider, "#7f8c8d"),
            label=display_name(provider),
        )
    ax.set_yticks(y, [display_name(provider) for provider in providers])
    for tick in ax.get_yticklabels():
        tick.set_color("#172027")
        tick.set_fontweight("normal")
    ax.set_xlim(0, 1)
    ax.set_xlabel(f"{METHOD_LABEL} nDCG@10")
    ax.set_title(method_title("nDCG@10 with 95% CI"))
    ax.invert_yaxis()
    ax.grid(axis="x", alpha=0.3)
    fig.text(0.01, 0.01, f"{METHOD_LABEL}; each dot is the mean nDCG@10 across 80 primary queries; whiskers show the paired-bootstrap 95% confidence interval.", fontsize=FOOTER_SIZE, color="#5c6870")
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(out_dir / "01_overall_quality_dot_whisker.png", dpi=180)
    plt.close(fig)


def save_category_heatmap(out_dir: Path, per_query: List[dict]) -> None:
    providers = provider_order(r["provider"] for r in per_query)
    categories = category_order(r["category"] for r in per_query)
    grouped = defaultdict(list)
    for row in per_query:
        grouped[(row["provider"], row["category"])].append(row["ndcg10"])

    matrix = np.array([
        [np.mean(grouped[(p, c)]) if grouped[(p, c)] else np.nan for c in categories]
        for p in providers
    ])

    fig, ax = plt.subplots(figsize=(14, 7.5))
    cmap = plt.get_cmap("YlGnBu")
    image = ax.imshow(matrix, aspect="auto", vmin=0, vmax=1, cmap=cmap)
    style_heatmap_axes(ax, providers, categories)
    ax.set_title(method_title("nDCG@10 by category and provider"), pad=8)
    annotate_heatmap(
        ax,
        matrix,
        cmap=cmap,
        vmin=0,
        vmax=1,
        formatter=lambda value, _row, _column: f"{value:.2f}",
    )
    finish_heatmap(
        fig,
        ax,
        image,
        out_dir / "02_category_performance_heatmap.png",
        colorbar_label="nDCG@10",
        footer=f"{METHOD_LABEL}; cell color encodes mean nDCG@10. Eight primary queries per category.",
    )


def save_win_tie_loss(out_dir: Path, per_query: List[dict], threshold: float = 0.05) -> None:
    by_provider_query = {(r["provider"], r["query_id"]): r["ndcg10"] for r in per_query}
    providers = sorted({r["provider"] for r in per_query})
    comparisons = []
    values = []

    for a, b in itertools.combinations(providers, 2):
        common = sorted({
            q for p, q in by_provider_query
            if p == a and (b, q) in by_provider_query
        })
        deltas = [by_provider_query[(a, q)] - by_provider_query[(b, q)] for q in common]
        wins = sum(d >= threshold for d in deltas)
        losses = sum(d <= -threshold for d in deltas)
        ties = len(deltas) - wins - losses
        total = max(1, len(deltas))
        comparisons.append(f"{display_name(a)} vs {display_name(b)}")
        values.append((wins / total, ties / total, losses / total))

    if not comparisons:
        return

    fig, ax = plt.subplots(figsize=(10, max(4, len(comparisons) * 0.65)))
    y = np.arange(len(comparisons))
    wins = np.array([v[0] for v in values])
    ties = np.array([v[1] for v in values])
    losses = np.array([v[2] for v in values])
    ax.barh(y, wins, label="First provider wins", color="#2166ac", hatch="", edgecolor="white")
    ax.barh(y, ties, left=wins, label="Tie", color="#bdbdbd", hatch="..", edgecolor="white")
    ax.barh(y, losses, left=wins + ties, label="First provider loses", color="#b2182b", hatch="//", edgecolor="white")
    ax.set_yticks(y, comparisons)
    ax.set_xlim(0, 1)
    ax.set_xlabel("Share of paired queries")
    ax.set_title(method_title(f"paired win-tie-loss (tie threshold +/-{threshold:.2f} nDCG@10)"))
    ax.grid(axis="x", alpha=0.25)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=3, frameon=False)
    fig.text(0.01, 0.01, f"{METHOD_LABEL}; blue means the first provider wins, red means it loses, and gray means tie. Hatching provides a second visual cue.", fontsize=FOOTER_SIZE, color="#5c6870")
    fig.tight_layout(rect=(0, 0.05, 1, 0.91))
    fig.savefig(out_dir / "03_pairwise_win_tie_loss.png", dpi=180)
    plt.close(fig)


def save_latency_ecdf(out_dir: Path, run_rows: List[dict]) -> None:
    grouped = defaultdict(list)
    for row in run_rows:
        status = as_int(row.get("http_status"))
        latency = as_float(row.get("latency_ms"))
        if 200 <= status < 300 and latency > 0:
            grouped[row.get("provider", "")].append(latency)

    if not grouped:
        return

    fig, ax = plt.subplots(figsize=(9, 6))
    for index, provider in enumerate(provider_order(grouped)):
        values = grouped[provider]
        x = np.sort(np.asarray(values))
        y = np.arange(1, len(x) + 1) / len(x)
        ax.step(
            x,
            y,
            where="post",
            label=f"{display_name(provider)} (n={len(values)})",
            color=BRAND_COLORS.get(provider, "#7f8c8d"),
            linestyle=("-", "--", "-.", ":")[index % 4],
        )

    ax.set_xscale("log")
    ax.set_ylim(0, 1.01)
    ax.set_xlabel("Latency (ms, log scale)")
    ax.set_ylabel("Fraction of successful requests completed")
    ax.set_title(chart_title("Latency distribution of successful requests"))
    ax.grid(alpha=0.3)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=3, frameon=False)
    fig.text(0.01, 0.01, "Successful requests only (HTTP 2xx, no error). Each step is one observed latency value.", fontsize=FOOTER_SIZE, color="#5c6870")
    fig.tight_layout(rect=(0, 0.05, 1, 0.91))
    fig.savefig(out_dir / "05_latency_ecdf.png", dpi=180)
    plt.close(fig)


def save_relevance_by_rank(out_dir: Path, score_rows: List[dict]) -> None:
    grouped = defaultdict(list)
    for row in score_rows:
        rank = as_int(row.get("rank"))
        if 1 <= rank <= 10:
            grouped[(row["provider"], rank)].append(as_float(row.get("relevance_grade")))

    providers = provider_order(p for p, _ in grouped)
    if not providers:
        return

    fig, ax = plt.subplots(figsize=(9, 6))
    ranks = list(range(1, 11))
    for index, provider in enumerate(providers):
        means = [
            np.mean(grouped[(provider, rank)]) if grouped[(provider, rank)] else np.nan
            for rank in ranks
        ]
        ax.plot(
            ranks,
            means,
            marker="o",
            label=display_name(provider),
            color=BRAND_COLORS.get(provider, "#7f8c8d"),
            linestyle=("-", "--", "-.", ":")[index % 4],
        )

    ax.set_xticks(ranks)
    ax.set_ylim(0, 3)
    ax.set_xlabel("Rank")
    ax.set_ylabel("Mean relevance grade")
    ax.set_title(method_title("relevance grade by result position"))
    ax.grid(alpha=0.3)
    ax.legend(loc="upper left", frameon=False)
    fig.text(0.01, 0.01, f"{METHOD_LABEL}; mean relevance grade at each rank position across all primary queries. Grade scale: 0 (off-topic) to 3 (highly relevant).", fontsize=FOOTER_SIZE, color="#5c6870")
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(out_dir / "06_mean_relevance_by_rank.png", dpi=180)
    plt.close(fig)


def save_overlap(out_dir: Path, score_rows: List[dict]) -> None:
    memberships = defaultdict(set)
    for row in score_rows:
        if as_float(row.get("relevance_grade")) < 2:
            continue
        qid = row["query_id"]
        url = row.get("canonical_url") or row.get("url")
        if not url:
            continue
        memberships[(qid, url)].add(row["provider"])

    counts = defaultdict(int)
    for providers in memberships.values():
        counts[tuple(sorted(providers))] += 1

    top = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:15]
    if not top:
        return

    labels = [" & ".join(display_name(provider) for provider in combo) for combo, _ in top]
    values = [count for _, count in top]
    fig, ax = plt.subplots(figsize=(11, max(5, len(top) * 0.45)))
    y = np.arange(len(top))
    ax.barh(y, values)
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    for bar_idx, val in enumerate(values):
        ax.text(val + 2, bar_idx, str(val), va="center", fontsize=8)
    ax.set_xlabel("Relevant query-URL pairs")
    ax.grid(axis="x", alpha=0.25)
    ax.set_title(method_title("relevant-result sharing and uniqueness"))
    fig.text(0.01, 0.01, f"{METHOD_LABEL}; top 15 nonzero provider combinations by count. Relevant results have grade >= 2, and each bar counts query-URL pairs found by exactly that combination, including singleton providers.", fontsize=FOOTER_SIZE, color="#5c6870")
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(out_dir / "07_relevant_result_overlap_upset_style.png", dpi=180)
    plt.close(fig)


def save_error_composition(out_dir: Path, run_rows: List[dict]) -> None:
    providers = provider_order(row.get("provider", "") for row in run_rows if row.get("provider"))
    error_types = ["timeout", "429", "5xx", "invalid_response", "other"]
    error_colors = {"timeout": "#e6ab02", "429": "#d95f02", "5xx": "#e7298a", "invalid_response": "#7570b3", "other": "#999999"}
    counts = {provider: {kind: 0 for kind in error_types} for provider in providers}

    for row in run_rows:
        provider = row.get("provider", "")
        if provider not in counts:
            continue
        status = as_int(row.get("http_status"))
        error = (row.get("error_type") or "").lower()
        if "timeout" in error or status == 408:
            kind = "timeout"
        elif status == 429 or "rate" in error:
            kind = "429"
        elif 500 <= status < 600:
            kind = "5xx"
        elif "invalid" in error or "parse" in error or "schema" in error:
            kind = "invalid_response"
        elif not (200 <= status < 300) or error:
            kind = "other"
        else:
            continue
        counts[provider][kind] += 1

    if not any(sum(v.values()) for v in counts.values()):
        return

    fig, ax = plt.subplots(figsize=(9, 6))
    x = np.arange(len(providers))
    bottom = np.zeros(len(providers))
    for kind in error_types:
        vals = np.array([counts[p][kind] for p in providers], dtype=float)
        totals = np.array([max(1, sum(counts[p].values())) for p in providers], dtype=float)
        shares = vals / totals
        ax.bar(x, shares, bottom=bottom, label=kind, color=error_colors[kind])
        bottom += shares

    ax.set_xticks(x, [display_name(provider) for provider in providers])
    ax.set_ylim(0, 1)
    ax.set_ylabel("Share of recorded failures")
    ax.set_title(chart_title("Error breakdown by failure type"))
    ax.grid(axis="y", alpha=0.25)
    ax.legend()
    fig.text(0.01, 0.01, "Share of total recorded failures per provider. Providers with zero failures are not shown.", fontsize=FOOTER_SIZE, color="#5c6870")
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(out_dir / "08_error_composition.png", dpi=180)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scores", required=True)
    parser.add_argument("--runs", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--title-prefix", default="")
    parser.add_argument("--method-label", default="Metadata-heuristic")
    args = parser.parse_args()
    global METHOD_LABEL, TITLE_PREFIX
    TITLE_PREFIX = args.title_prefix
    METHOD_LABEL = args.method_label

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    score_rows = read_csv(args.scores)
    run_rows = read_csv(args.runs)

    if not score_rows:
        raise SystemExit("The scored-results file has no rows.")
    per_query, _, _ = build_metrics(score_rows)
    summary = write_summary(out_dir, per_query, run_rows)

    save_overall_quality(out_dir, summary)
    save_category_heatmap(out_dir, per_query)
    save_win_tie_loss(out_dir, per_query)
    save_latency_ecdf(out_dir, run_rows)
    save_relevance_by_rank(out_dir, score_rows)
    save_overlap(out_dir, score_rows)
    save_error_composition(out_dir, run_rows)

    print(f"Wrote charts and metric tables to {out_dir}")


if __name__ == "__main__":
    main()
