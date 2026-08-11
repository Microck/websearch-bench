#!/usr/bin/env python3
"""Generate the key decision-useful charts from the shared benchmark run."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from chart_style import (
    BRAND_COLORS,
    DISPLAY_NAMES,
    FOOTER_SIZE,
    HEATMAP_FIGSIZE,
    HEATMAP_MUTED,
    HEATMAP_TEXT,
    annotate_heatmap,
    finish_heatmap,
    provider_order,
    style_heatmap_axes,
)
from make_octen_anchor_charts import (
    OCTEN_CATEGORY_LABELS,
    OCTEN_CATEGORY_ORDER,
    get_gold_urls_for_query,
    is_hit,
    load_data,
)


TEXT = HEATMAP_TEXT
MUTED = HEATMAP_MUTED
GRID = "#eeeeee"


def display_name(provider: str) -> str:
    return DISPLAY_NAMES.get(provider, provider)


def finish_standard_chart(
    fig: plt.Figure,
    ax: plt.Axes,
    output: Path,
    footer: str,
) -> None:
    ax.grid(axis="x", color=GRID)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        if spine != ax.spines["bottom"]:
            spine.set_visible(False)
    ax.tick_params(left=False)
    ax.text(
        0,
        -0.16,
        footer,
        transform=ax.transAxes,
        fontsize=FOOTER_SIZE,
        color=MUTED,
        va="top",
        clip_on=False,
    )
    fig.savefig(output, dpi=180, facecolor="white", bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)


def make_overall_hit_rate(summary: pd.DataFrame, output: Path) -> None:
    providers = provider_order(summary["provider"].unique())
    data = summary.set_index("provider").loc[providers]
    data = data.sort_values("hit_at_10_rate", ascending=True)

    fig, ax = plt.subplots(figsize=(8.5, 4.4))
    for index, provider in enumerate(data.index):
        hit_at_1 = float(data.loc[provider, "hit_at_1_rate"]) * 100
        hit_at_10 = float(data.loc[provider, "hit_at_10_rate"]) * 100
        color = BRAND_COLORS.get(provider, "#333333")
        ax.plot([hit_at_1, hit_at_10], [index, index], color=color, alpha=0.5, zorder=1)
        ax.scatter(hit_at_1, index, color=color, s=42, zorder=2)
        ax.scatter(hit_at_10, index, color=color, s=125, zorder=2)

    ax.set_yticks(range(len(data)), [display_name(provider) for provider in data.index])
    ax.set_xlim(0, 105)
    ax.set_xlabel("Gold URL hit rate (%)")
    ax.set_title("Overall retrieval success: hit@1 to hit@10")
    finish_standard_chart(
        fig,
        ax,
        output,
        footer=(
            f"{int(summary['graded_queries'].max())} graded queries; small dot = hit@1, "
            "large dot = hit@10."
        ),
    )


def make_quality_latency(summary: pd.DataFrame, runs: pd.DataFrame, output: Path) -> None:
    providers = provider_order(summary["provider"].unique())
    latency = runs.groupby("provider")["latency_ms"].agg(
        p95=lambda values: values.quantile(0.95)
    )
    data = summary.set_index("provider").loc[providers, ["hit_at_10_rate"]].join(latency)

    fig, ax = plt.subplots(figsize=(9.2, 5.6))
    # These fixed positions keep the dense high-quality cluster readable.
    label_positions = {
        "brave": (1020, 97),
        "context": (6650, 94),
        "exa": (2050, 103),
        "kagi_session": (2070, 88),
        "octen": (1130, 67),
        "parallel": (1250, 87),
        "perplexity": (1550, 95),
        "tavily": (3780, 94),
    }
    for provider in data.index:
        hit_at_10 = float(data.loc[provider, "hit_at_10_rate"]) * 100
        p95 = float(data.loc[provider, "p95"])
        ax.scatter(
            p95,
            hit_at_10,
            color=BRAND_COLORS.get(provider, "#333333"),
            s=82,
            edgecolors="white",
            linewidths=0.8,
            zorder=3,
        )
        ax.annotate(
            f"{display_name(provider)} {hit_at_10:.0f}%",
            (p95, hit_at_10),
            xytext=label_positions.get(provider, (p95 + 100, hit_at_10 + 3)),
            textcoords="data",
            arrowprops={"arrowstyle": "-", "color": "#9aa3a8", "linewidth": 0.6},
            fontsize=8,
            color=TEXT,
            va="center",
        )

    ax.set_xlim(0, max(float(data["p95"].max()) * 1.12, 7000))
    ax.set_ylim(0, 108)
    ax.set_xlabel("p95 latency (ms; lower is better)")
    ax.set_ylabel("Gold URL hit@10 (%)")
    ax.set_title("Retrieval success versus tail latency")
    ax.grid(color=GRID)
    ax.set_axisbelow(True)
    ax.text(
        0,
        -0.14,
        "Each point is one provider; x = p95 latency and y = hit@10. Colors identify providers only.",
        transform=ax.transAxes,
        fontsize=FOOTER_SIZE,
        color=MUTED,
        va="top",
        clip_on=False,
    )
    fig.savefig(output, dpi=180, facecolor="white", bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)


def category_hit_rates(
    queries: dict,
    queries_df: pd.DataFrame,
    results: pd.DataFrame,
    max_rank: int,
) -> tuple[pd.DataFrame, pd.Series]:
    providers = provider_order(results["provider"].unique())
    category_queries = queries_df.groupby("category")["query_id"].apply(list).to_dict()
    top_results = results[results["rank"] <= max_rank]
    result_groups = {
        key: group for key, group in top_results.groupby(["provider", "query_id"])
    }
    gold_by_query = {
        query_id: get_gold_urls_for_query(query)
        for query_id, query in queries.items()
    }
    data: list[dict[str, object]] = []

    for provider in providers:
        for category in OCTEN_CATEGORY_ORDER:
            query_ids = category_queries.get(category, [])
            hits = 0
            for query_id in query_ids:
                rows = result_groups.get((provider, query_id))
                if rows is None:
                    continue
                gold_urls, match_type = gold_by_query[query_id]
                if any(is_hit(url, gold_urls, match_type) for url in rows["url"]):
                    hits += 1
            data.append(
                {
                    "provider": provider,
                    "category": category,
                    "rate": 100 * hits / len(query_ids) if query_ids else np.nan,
                }
            )

    rates = pd.DataFrame(data).pivot(index="provider", columns="category", values="rate")
    rates = rates.reindex(index=providers, columns=OCTEN_CATEGORY_ORDER)
    counts = queries_df.groupby("category")["query_id"].nunique().reindex(OCTEN_CATEGORY_ORDER)
    return rates, counts


def make_category_heatmap(
    queries: dict,
    queries_df: pd.DataFrame,
    results: pd.DataFrame,
    max_rank: int,
    cmap,
    output: Path,
) -> None:
    rates, counts = category_hit_rates(queries, queries_df, results, max_rank=max_rank)
    categories = OCTEN_CATEGORY_ORDER
    providers = list(rates.index)
    values = rates.to_numpy(dtype=float)
    metric_label = f"hit@{max_rank}"

    fig, ax = plt.subplots(figsize=HEATMAP_FIGSIZE)
    image = ax.imshow(values, cmap=cmap, vmin=0, vmax=100, aspect="auto")
    style_heatmap_axes(ax, providers, categories)
    ax.set_xticklabels(
        [
            f"{OCTEN_CATEGORY_LABELS.get(category, category)} (n={int(counts[category])})"
            for category in categories
        ]
    )
    ax.set_title(f"{metric_label} by category and provider", pad=8)
    annotate_heatmap(
        ax,
        values,
        cmap=cmap,
        vmin=0,
        vmax=100,
        formatter=lambda value, _row, _column: f"{value:.0f}%",
    )
    finish_heatmap(
        fig,
        ax,
        image,
        output,
        colorbar_label=f"Gold URL {metric_label} (%)",
        footer=(
            f"Rows are providers; cell color and text show {metric_label}. "
            "n = queries in each category."
        ),
        footer_x=0.5,
        footer_y=-0.23,
        footer_ha="center",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    queries, queries_df, runs, results, summary = load_data(args.run_dir)
    runs["latency_ms"] = pd.to_numeric(runs["latency_ms"], errors="raise")
    results["rank"] = pd.to_numeric(results["rank"], errors="raise")

    make_overall_hit_rate(summary, args.out / "01-overall-retrieval-success.png")
    make_quality_latency(summary, runs, args.out / "02-quality-versus-tail-latency.png")
    make_category_heatmap(
        queries,
        queries_df,
        results,
        max_rank=1,
        cmap=plt.cm.Oranges,
        output=args.out / "03-category-hit-at-1-heatmap.png",
    )
    make_category_heatmap(
        queries,
        queries_df,
        results,
        max_rank=10,
        cmap=plt.cm.Greens,
        output=args.out / "04-category-hit-at-10-heatmap.png",
    )

    outputs = sorted(args.out.glob("*.png"))
    if len(outputs) != 4 or any(path.stat().st_size == 0 for path in outputs):
        raise SystemExit("Key chart output validation failed")
    print(f"Wrote {len(outputs)} key charts to {args.out}")


if __name__ == "__main__":
    main()
