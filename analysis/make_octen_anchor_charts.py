import argparse
import json
import logging
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from chart_style import (
    BRAND_COLORS, DISPLAY_NAMES, FOOTER_SIZE, HEATMAP_FIGSIZE,
    annotate_heatmap, finish_heatmap, provider_order, style_heatmap_axes,
    style_provider_matrix_axes,
)

try:
    from runners.run_benchmark import canonicalize_url
except ModuleNotFoundError:  # pragma: no cover - supports direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from runners.run_benchmark import canonicalize_url

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

OCTEN_CATEGORY_ORDER = [
    "tech_code", "academic", "finance", "medical_legal",
    "local", "news", "ecommerce", "entertainment", "general",
]
OCTEN_CATEGORY_LABELS = {
    "tech_code": "Tech and code",
    "academic": "Academic",
    "finance": "Finance",
    "medical_legal": "Medical and legal",
    "local": "Local",
    "news": "News",
    "ecommerce": "Ecommerce",
    "entertainment": "Entertainment",
    "general": "General",
}

def load_data(run_dir: Path):
    queries_df = pd.read_csv(run_dir / "anchor_queries.csv")
    with open(run_dir / "anchor_queries.jsonl") as f:
        queries_json = [json.loads(line) for line in f]
    
    queries = {q['qid']: q for q in queries_json}
    
    runs_df = pd.read_csv(run_dir / "runs.csv")
    results_df = pd.read_csv(run_dir / "retrieved_results.csv")
    summary_df = pd.read_csv(run_dir / "anchor_summary.csv")

    required_summary = {"provider", "hit_at_1_rate", "hit_at_10_rate"}
    missing_summary = sorted(required_summary - set(summary_df.columns))
    if missing_summary:
        raise SystemExit(
            "anchor_summary.csv is missing columns: " + ", ".join(missing_summary)
        )

    return queries, queries_df, runs_df, results_df, summary_df

def plot_overall_hit_rate(summary_df, out_dir):
    providers = provider_order(summary_df["provider"].unique())
    summary_df = summary_df.set_index("provider").loc[providers]
    summary_df = summary_df.sort_values("hit_at_10_rate", ascending=True)
    
    fig, ax = plt.subplots(figsize=(8, 4))
    
    for i, provider in enumerate(summary_df.index):
        hit1 = summary_df.loc[provider, "hit_at_1_rate"] * 100
        hit10 = summary_df.loc[provider, "hit_at_10_rate"] * 100
        color = BRAND_COLORS.get(provider, "#333333")
        
        ax.plot([hit1, hit10], [i, i], color=color, alpha=0.5, zorder=1)
        ax.scatter(hit1, i, color=color, s=40, zorder=2)
        ax.scatter(hit10, i, color=color, s=120, zorder=2)
    
    ax.set_yticks(range(len(summary_df)))
    ax.set_yticklabels([DISPLAY_NAMES.get(p, p) for p in summary_df.index])
    ax.set_xlim(0, 100)
    ax.set_xlabel("Hit Rate (%)")
    ax.set_title("Overall Hit Rate: hit@1 (small) and hit@10 (large)")
    
    for spine in ax.spines.values():
        if spine != ax.spines["bottom"]:
            spine.set_visible(False)
    ax.grid(axis="x", color="#eeeeee")
    ax.tick_params(left=False)
    
    plt.text(0, -0.15, "Hit@1 and Hit@10 rates based on gold URL matching.", 
             transform=ax.transAxes, fontsize=FOOTER_SIZE, color="#5c6870")
    
    fig.savefig(out_dir / "01_overall_quality_dot_whisker.png", dpi=180, facecolor="white", bbox_inches="tight")
    plt.close(fig)

def get_gold_urls_for_query(query):
    gold = query.get('gold', {})
    urls = gold.get('gold_urls', [])
    match_type = gold.get('url_match', 'exact')
    return [canonicalize_url(u) for u in urls], match_type

def is_hit(result_url, gold_urls, match_type):
    if pd.isna(result_url):
        return False
    canon_res = canonicalize_url(result_url)
    for g in gold_urls:
        if match_type == 'prefix' and canon_res.startswith(g):
            return True
        elif canon_res == g:
            return True
    return False

def plot_category_hit_rate(queries, queries_df, results_df, out_dir):
    providers = provider_order(results_df["provider"].unique())
    categories = OCTEN_CATEGORY_ORDER
    
    # Calculate hit@10 per provider per category
    data = []
    
    query_to_cat = queries_df.set_index("query_id")["category"].to_dict()
    
    for provider in providers:
        prov_results = results_df[results_df["provider"] == provider]
        for category in categories:
            cat_queries = [q for q, c in query_to_cat.items() if c == category]
            hits = 0
            for q_id in cat_queries:
                golds, match_type = get_gold_urls_for_query(queries[q_id])
                top10 = prov_results[(prov_results["query_id"] == q_id) & (prov_results["rank"] <= 10)]
                for _, row in top10.iterrows():
                    if is_hit(row["url"], golds, match_type):
                        hits += 1
                        break
            
            rate = (hits / len(cat_queries)) * 100 if cat_queries else 0
            data.append({"provider": provider, "category": category, "hit_at_10": rate})
    
    df = pd.DataFrame(data).pivot(index="provider", columns="category", values="hit_at_10")
    df = df.reindex(index=providers, columns=categories)
    
    fig, ax = plt.subplots(figsize=HEATMAP_FIGSIZE)
    style_heatmap_axes(ax, providers, categories)
    ax.set_xticklabels([OCTEN_CATEGORY_LABELS.get(c, c) for c in categories])
    
    cmap = plt.cm.Greens
    img = ax.imshow(df.values, cmap=cmap, vmin=0, vmax=100, aspect="auto")
    
    annotate_heatmap(ax, df.values, cmap=cmap, vmin=0, vmax=100, 
                     formatter=lambda v, r, c: f"{v:.0f}%")
    
    ax.set_title("Category Performance (Hit@10 %)")
    finish_heatmap(fig, ax, img, out_dir / "02_category_performance_heatmap.png", 
                   colorbar_label="Hit@10 %", footer="Hit@10 based on gold URL matching.")

def plot_latency_summary(runs_df, out_dir):
    providers = provider_order(runs_df["provider"].unique())
    
    stats = []
    for provider in providers:
        prov_runs = runs_df[runs_df["provider"] == provider]["latency_ms"]
        stats.append({
            "provider": provider,
            "p50": prov_runs.median(),
            "p95": prov_runs.quantile(0.95)
        })
    
    stats_df = pd.DataFrame(stats).set_index("provider").sort_values("p50", ascending=False)
    
    fig, ax = plt.subplots(figsize=(8, 5))
    y = np.arange(len(stats_df))
    height = 0.35
    
    for i, provider in enumerate(stats_df.index):
        color = BRAND_COLORS.get(provider, "#333333")
        ax.barh(y[i] - height/2, stats_df.loc[provider, "p50"], height, color=color, alpha=1.0)
        ax.barh(y[i] + height/2, stats_df.loc[provider, "p95"], height, color=color, alpha=0.5)
        
    ax.set_yticks(y)
    ax.set_yticklabels([DISPLAY_NAMES.get(p, p) for p in stats_df.index])
    ax.set_xlabel("Latency (ms)")
    ax.set_title("Latency: p50 (dark) and p95 (light)")
    
    for spine in ax.spines.values():
        if spine != ax.spines["bottom"]:
            spine.set_visible(False)
    ax.grid(axis="x", color="#eeeeee")
    ax.tick_params(left=False)
    
    plt.text(0, -0.15, "Latency per provider.", transform=ax.transAxes, fontsize=FOOTER_SIZE, color="#5c6870")
    
    fig.savefig(out_dir / "03_latency_summary.png", dpi=180, facecolor="white", bbox_inches="tight")
    plt.close(fig)

def plot_result_depth(results_df, out_dir):
    providers = provider_order(results_df["provider"].unique())
    
    counts = results_df.groupby(["provider", "query_id"]).size().reset_index(name="count")
    means = counts.groupby("provider")["count"].mean().reindex(providers).sort_values(ascending=False)
    
    fig, ax = plt.subplots(figsize=(8, 4))
    
    y = np.arange(len(means))
    colors = [BRAND_COLORS.get(p, "#333333") for p in means.index]
    ax.barh(y, means.values, color=colors)
    
    ax.set_yticks(y)
    ax.set_yticklabels([DISPLAY_NAMES.get(p, p) for p in means.index])
    ax.set_xlabel("Mean Results Returned")
    ax.set_xlim(0, 10)
    ax.set_title("Result Depth")
    
    for spine in ax.spines.values():
        if spine != ax.spines["bottom"]:
            spine.set_visible(False)
    ax.grid(axis="x", color="#eeeeee")
    ax.tick_params(left=False)
    
    plt.text(0, -0.15, "Mean number of results returned out of 10.", transform=ax.transAxes, fontsize=FOOTER_SIZE, color="#5c6870")
    
    fig.savefig(out_dir / "04_result_depth.png", dpi=180, facecolor="white", bbox_inches="tight")
    plt.close(fig)

def plot_category_latency(runs_df, queries_df, out_dir):
    providers = provider_order(runs_df["provider"].unique())
    categories = OCTEN_CATEGORY_ORDER
    
    runs = runs_df.merge(queries_df[["query_id", "category"]], on="query_id")
    
    data_p50 = []
    data_p95 = []
    for provider in providers:
        for category in categories:
            subset = runs[(runs["provider"] == provider) & (runs["category"] == category)]["latency_ms"]
            if len(subset) > 0:
                data_p50.append({"provider": provider, "category": category, "val": subset.median()})
                data_p95.append({"provider": provider, "category": category, "val": subset.quantile(0.95)})
            else:
                data_p50.append({"provider": provider, "category": category, "val": np.nan})
                data_p95.append({"provider": provider, "category": category, "val": np.nan})
                
    df_p50 = pd.DataFrame(data_p50).pivot(index="provider", columns="category", values="val").reindex(index=providers, columns=categories)
    df_p95 = pd.DataFrame(data_p95).pivot(index="provider", columns="category", values="val").reindex(index=providers, columns=categories)
    
    fig, ax = plt.subplots(figsize=HEATMAP_FIGSIZE)
    style_heatmap_axes(ax, providers, categories)
    ax.set_xticklabels([OCTEN_CATEGORY_LABELS.get(c, c) for c in categories])
    
    cmap = plt.cm.YlGnBu
    img = ax.imshow(df_p50.values, cmap=cmap, aspect="auto")
    
    annotate_heatmap(ax, df_p50.values, cmap=cmap, vmin=df_p50.min().min(), vmax=df_p50.max().max(), 
                     formatter=lambda v, r, c: f"{v:.0f}\n({df_p95.iloc[r, c]:.0f})")
    
    ax.set_title("Category Latency (ms): p50 (p95)")
    finish_heatmap(fig, ax, img, out_dir / "05_category_latency_heatmap.png", 
                   colorbar_label="p50 Latency (ms)", footer="Median latency (ms).")

def plot_raw_result_agreement(results_df, out_dir):
    providers = provider_order(results_df["provider"].unique())
    query_ids = results_df["query_id"].unique()
    
    matrix = np.zeros((len(providers), len(providers)))
    
    prov_url_sets = {}
    for p in providers:
        prov_url_sets[p] = {}
        for q in query_ids:
            urls = results_df[(results_df["provider"] == p) & (results_df["query_id"] == q) & (results_df["rank"] <= 10)]["canonical_url"].dropna().tolist()
            prov_url_sets[p][q] = set(urls)
            
    for i, p1 in enumerate(providers):
        for j, p2 in enumerate(providers):
            if i == j:
                matrix[i, j] = 1.0
            elif i < j:
                sims = []
                for q in query_ids:
                    s1 = prov_url_sets[p1].get(q, set())
                    s2 = prov_url_sets[p2].get(q, set())
                    if not s1 and not s2:
                        continue
                    sim = len(s1.intersection(s2)) / len(s1.union(s2))
                    sims.append(sim)
                val = np.mean(sims) if sims else 0
                matrix[i, j] = val
                matrix[j, i] = val
                
    fig, ax = plt.subplots(figsize=(8, 6))
    style_provider_matrix_axes(ax, providers)
    
    cmap = plt.cm.Blues
    img = ax.imshow(matrix, cmap=cmap, vmin=0, vmax=1.0)
    
    annotate_heatmap(ax, matrix, cmap=cmap, vmin=0, vmax=1.0, 
                     formatter=lambda v, r, c: f"{v:.2f}")
    
    ax.set_title("Raw Result Agreement (Jaccard Similarity)")
    finish_heatmap(fig, ax, img, out_dir / "06_raw_result_agreement.png", 
                   colorbar_label="Jaccard Similarity", footer="Jaccard similarity of top-10 canonical URLs.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=Path("runs/octen-anchors-20260809"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    
    args.out.mkdir(parents=True, exist_ok=True)
    
    logging.info(f"Loading data from {args.run_dir}")
    queries, queries_df, runs_df, results_df, summary_df = load_data(args.run_dir)
    
    logging.info("Generating Overall Hit Rate")
    plot_overall_hit_rate(summary_df, args.out)
    
    logging.info("Generating Category Hit Rate Heatmap")
    plot_category_hit_rate(queries, queries_df, results_df, args.out)
    
    logging.info("Generating Latency Summary")
    plot_latency_summary(runs_df, args.out)
    
    logging.info("Generating Result Depth")
    plot_result_depth(results_df, args.out)
    
    logging.info("Generating Category Latency Heatmap")
    plot_category_latency(runs_df, queries_df, args.out)
    
    logging.info("Generating Raw Result Agreement")
    plot_raw_result_agreement(results_df, args.out)
    
    logging.info(f"Done! Output in {args.out}")

if __name__ == "__main__":
    main()
