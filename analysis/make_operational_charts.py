#!/usr/bin/env python3
"""Generate operational charts from a completed benchmark repetition.

This script intentionally does not create relevance or quality scores. Those
metrics require the blind judgment files to be completed first.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt

try:
    from chart_style import BRAND_COLORS, add_watermark
except ImportError:  # pragma: no cover - supports package-style imports
    from .chart_style import BRAND_COLORS, add_watermark


PROVIDER_ORDER = ["brave", "context", "exa", "kagi_session", "octen", "parallel", "perplexity", "tavily"]
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
COLORS = BRAND_COLORS
def provider_order(values: pd.Series) -> list[str]:
    present = {str(value) for value in values.dropna()}
    order = [provider for provider in PROVIDER_ORDER if provider in present]
    order.extend(sorted(present - set(order)))
    return order


def load_inputs(run_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    summary = pd.read_csv(run_dir / "run_summary.csv")
    runs = pd.read_csv(run_dir / "runs.csv")
    billing = pd.read_csv(run_dir / "provider_billing_estimates.csv")

    required_summary = {
        "provider",
        "requests",
        "successful_requests",
        "mean_results_per_request",
        "short_result_requests",
        "p50_latency_ms",
        "p95_latency_ms",
        "total_cost_usd_reported",
    }
    required_runs = {"provider", "latency_ms", "http_status", "error_type"}
    required_billing = {"scope", "provider", "estimated_cost_usd", "rate_basis"}
    for label, frame, required in (
        ("run_summary.csv", summary, required_summary),
        ("runs.csv", runs, required_runs),
        ("provider_billing_estimates.csv", billing, required_billing),
    ):
        missing = sorted(required - set(frame.columns))
        if missing:
            raise SystemExit(f"{label} is missing columns: {', '.join(missing)}")

    for column in (
        "requests",
        "successful_requests",
        "mean_results_per_request",
        "short_result_requests",
        "p50_latency_ms",
        "p95_latency_ms",
        "total_cost_usd_reported",
    ):
        summary[column] = pd.to_numeric(summary[column], errors="raise")
    runs["latency_ms"] = pd.to_numeric(runs["latency_ms"], errors="coerce")
    runs["http_status"] = pd.to_numeric(runs["http_status"], errors="coerce")
    billing["estimated_cost_usd"] = pd.to_numeric(billing["estimated_cost_usd"], errors="raise")
    return summary, runs, billing


def ordered_summary(summary: pd.DataFrame) -> pd.DataFrame:
    order = provider_order(summary["provider"])
    result = summary.set_index("provider").loc[order].reset_index()
    result["label"] = result["provider"].map(DISPLAY_NAMES).fillna(result["provider"])
    result["color"] = result["provider"].map(COLORS).fillna("#7f8c8d")
    return result


def save_figure(fig: plt.Figure, path: Path) -> None:
    add_watermark(fig)
    fig.savefig(path, dpi=180, bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)


def make_latency_chart(summary: pd.DataFrame, out_dir: Path) -> None:
    data = ordered_summary(summary)
    x = np.arange(len(data))
    width = 0.36
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(x - width / 2, data["p50_latency_ms"], width, label="p50", color="#3a7ca5")
    ax.bar(x + width / 2, data["p95_latency_ms"], width, label="p95", color="#d4856a")
    ax.set_xticks(x, data["label"])
    ax.set_ylabel("Latency (ms)")
    ax.set_title("Response latency: median and p95")
    ax.grid(axis="y", alpha=0.25)
    ax.legend()
    for index, row in data.iterrows():
        ax.text(index - width / 2, row["p50_latency_ms"] + 55, f"{row['p50_latency_ms']:.0f}", ha="center", fontsize=8)
        ax.text(index + width / 2, row["p95_latency_ms"] + 55, f"{row['p95_latency_ms']:.0f}", ha="center", fontsize=8)
    fig.text(0.01, 0.01, "All 96 requests per provider: 80 primary and 16 reference queries. Successful HTTP 2xx requests only.", fontsize=8, color="#5c6870")
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    save_figure(fig, out_dir / "latency-summary.png")


def make_depth_chart(summary: pd.DataFrame, out_dir: Path) -> None:
    data = ordered_summary(summary)
    data["short_list_pct"] = 100 * data["short_result_requests"] / data["requests"]
    x = np.arange(len(data))

    depth_fig, depth_ax = plt.subplots(figsize=(5.5, 4.5))
    depth_ax.bar(x, data["mean_results_per_request"], color=data["color"])
    depth_ax.set_xticks(x, data["label"], rotation=20, ha="right")
    depth_ax.set_ylim(0, 10.5)
    depth_ax.set_ylabel("Mean results per request")
    depth_ax.set_title("Result depth")
    depth_ax.grid(axis="y", alpha=0.25)
    for index, value in enumerate(data["mean_results_per_request"]):
        depth_ax.text(index, value + 0.15, f"{value:.2f}", ha="center", fontsize=8)
    depth_fig.text(0.01, 0.01, "All 96 requests per provider: 80 primary and 16 reference queries.", fontsize=8, color="#5c6870")
    depth_fig.tight_layout(rect=(0, 0.06, 1, 1))
    save_figure(depth_fig, out_dir / "result-depth.png")

    short_list_fig, short_list_ax = plt.subplots(figsize=(5.5, 4.5))
    short_list_ax.bar(x, data["short_list_pct"], color=data["color"])
    short_list_ax.set_xticks(x, data["label"], rotation=20, ha="right")
    short_list_ax.set_ylim(0, 100)
    short_list_ax.set_ylabel("Requests returning fewer than 10 results (%)")
    short_list_ax.set_title("Short-list rate")
    short_list_ax.grid(axis="y", alpha=0.25)
    for index, value in enumerate(data["short_list_pct"]):
        short_list_ax.text(index, value + 2, f"{value:.1f}%", ha="center", fontsize=8)
    short_list_fig.text(0.01, 0.01, "96 requests per provider. Fewer than 10 results counts as a short list.", fontsize=8, color="#5c6870")
    short_list_fig.tight_layout(rect=(0, 0.06, 1, 1))
    save_figure(short_list_fig, out_dir / "short-list-rate.png")


def benchmark_costs(summary: pd.DataFrame, billing: pd.DataFrame) -> pd.DataFrame:
    data = ordered_summary(summary)[["provider", "label", "color", "total_cost_usd_reported"]].copy()
    data["cost_usd"] = pd.to_numeric(data.pop("total_cost_usd_reported"), errors="coerce")
    benchmark = billing[billing["scope"] == "benchmark repetition 1"]
    for provider in data["provider"]:
        row = data["provider"] == provider
        estimate = benchmark[benchmark["provider"] == provider]
        if estimate.empty:
            data.loc[row, "cost_usd"] = np.nan
            data.loc[row, "mode"] = "not supplied"
        else:
            data.loc[row, "cost_usd"] = float(estimate.iloc[0]["estimated_cost_usd"])
            data.loc[row, "mode"] = str(estimate.iloc[0].get("cost_mode", "estimate"))
    return data


def make_cost_chart(summary: pd.DataFrame, billing: pd.DataFrame, out_dir: Path) -> None:
    data = benchmark_costs(summary, billing)
    x = np.arange(len(data))
    dpi = 180
    width_px, height_px = 989, 812
    fig, ax = plt.subplots(figsize=(width_px / dpi, height_px / dpi))
    bars = ax.bar(x, data["cost_usd"].fillna(0), color=data["color"])
    ax.set_xticks(x, data["label"], rotation=20, ha="right")
    ax.set_ylabel("Cost for 96 benchmark queries (USD)")
    ax.set_title("Cost for 96 benchmark queries")
    ax.grid(axis="y", alpha=0.25)
    finite_costs = data["cost_usd"].dropna()
    max_cost = float(finite_costs.max()) if not finite_costs.empty else 1.0
    label_offset = max(0.02, max_cost * 0.035)
    ax.set_ylim(0, max(1.0, max_cost * 1.28))
    for bar, (_, row) in zip(bars, data.iterrows()):
        if pd.isna(row["cost_usd"]):
            label = "n/a (rate not supplied)"
            y = label_offset
        else:
            label = f"${row['cost_usd']:.3f}"
            y = bar.get_height() + label_offset
        ax.text(bar.get_x() + bar.get_width() / 2, y, label, ha="center", fontsize=8)
    fig.subplots_adjust(bottom=0.20, left=0.14, right=0.98, top=0.91)
    add_watermark(fig)
    fig.savefig(out_dir / "cost-comparison.png", dpi=dpi, facecolor="white")
    plt.close(fig)


def successful_runs(runs: pd.DataFrame) -> pd.DataFrame:
    status_ok = runs["http_status"].between(200, 299)
    no_error = runs["error_type"].fillna("").eq("")
    return runs[status_ok & no_error & runs["latency_ms"].notna() & (runs["latency_ms"] > 0)]


def make_latency_ecdf(runs: pd.DataFrame, out_dir: Path) -> None:
    data = successful_runs(runs)
    fig, ax = plt.subplots(figsize=(9, 5))
    for provider in provider_order(runs["provider"]):
        values = np.sort(data.loc[data["provider"] == provider, "latency_ms"].to_numpy())
        if len(values) == 0:
            continue
        y = np.arange(1, len(values) + 1) / len(values)
        ax.step(values, y, where="post", label=f"{DISPLAY_NAMES.get(provider, provider)} (n={len(values)})", color=COLORS.get(provider, "#7f8c8d"))
    ax.set_xscale("log")
    ax.set_ylim(0, 1.01)
    ax.set_xlabel("Latency (ms, log scale)")
    ax.set_ylabel("Fraction of successful requests")
    ax.set_title("Latency distribution of successful requests")
    ax.grid(alpha=0.25)
    ax.legend()
    fig.text(0.01, 0.01, "Successful requests only (HTTP 2xx, no error) across the 80 primary and 16 reference queries. Each step is one observed latency.", fontsize=8, color="#5c6870")
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    save_figure(fig, out_dir / "latency-ecdf.png")


def write_chart_data(summary: pd.DataFrame, billing: pd.DataFrame, out_dir: Path) -> None:
    data = ordered_summary(summary)
    costs = benchmark_costs(summary, billing)[["provider", "cost_usd", "mode"]]
    data["short_list_pct"] = 100 * data["short_result_requests"] / data["requests"]
    result = data[["provider", "requests", "successful_requests", "mean_results_per_request", "short_result_requests", "short_list_pct", "p50_latency_ms", "p95_latency_ms"]].merge(costs, on="provider")
    result.to_csv(out_dir / "chart-data.csv", index=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    summary, runs, billing = load_inputs(args.run_dir)
    make_latency_chart(summary, args.out)
    make_depth_chart(summary, args.out)
    make_cost_chart(summary, billing, args.out)
    make_latency_ecdf(runs, args.out)
    write_chart_data(summary, billing, args.out)
    outputs = sorted(path for path in args.out.iterdir() if path.is_file())
    if not outputs or any(path.stat().st_size == 0 for path in outputs):
        raise SystemExit("Chart output validation failed")
    print(f"Wrote {len(outputs)} chart artifacts to {args.out}")


if __name__ == "__main__":
    main()
