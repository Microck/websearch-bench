#!/usr/bin/env python3
"""Generate heuristic and blind-AI quality-versus-cost/latency charts."""

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
from pathlib import Path
try:
    from chart_style import BRAND_COLORS, DISPLAY_NAMES, FOOTER_SIZE, HEATMAP_MUTED, HEATMAP_TEXT
except ImportError:  # pragma: no cover - supports package-style imports
    from .chart_style import BRAND_COLORS, DISPLAY_NAMES, FOOTER_SIZE, HEATMAP_MUTED, HEATMAP_TEXT

TARGET_QUALITY = 0.55
TARGET_BAND_COLOR = "#e8f2eb"
TARGET_LINE_COLOR = "#9ab8bd"

def pareto_frontier_step(points: list, x_key: str, y_key: str):
    """
    Computes non-dominated Pareto frontier points for discrete vendors.
    Vendor A dominates Vendor B if A.x <= B.x and A.y >= B.y (with at least one strict inequality).
    """
    frontier = []
    for cand in points:
        cx, cy = cand[x_key], cand[y_key]
        dominated = False
        for oth in points:
            if oth["provider"] == cand["provider"]:
                continue
            ox, oy = oth[x_key], oth[y_key]
            if ox <= cx and oy >= cy and (ox < cx or oy > cy):
                dominated = True
                break
        if not dominated:
            frontier.append(cand)
            
    frontier.sort(key=lambda p: p[x_key])
    return frontier

def create_pareto_chart(
    data: list,
    x_metric: str,  # 'cost' or 'latency'
    out_path: Path,
    quality_label: str,
    y_axis_label: str,
    include_frontier: bool = False,
):
    fig, ax = plt.subplots(figsize=(9, 5.5))
    
    x_key = "cost_1k" if x_metric == "cost" else "p95_ms"
    y_key = "ndcg10"
    
    if x_metric == "cost":
        title = f"{quality_label} versus cost"
        x_label = "Cost per 1,000 queries (USD, log scale)"
        footer = "Cost bases are provider-specific. Shaded band and target line are illustrative."
    else:
        title = f"{quality_label} versus p95 latency"
        x_label = "p95 latency (ms, log scale)"
        footer = "p95 is HTTP latency. Shaded band and target line are illustrative."
    if include_frontier:
        footer += " Dashed line = illustrative point-estimate frontier."
    footer += " Whiskers = paired-bootstrap 95% CI."

    # Full non-misleading Y-axis scale (0.0 to 1.0)
    ax.set_ylim(0.0, 1.0)
    ax.set_yticks(np.arange(0.0, 1.1, 0.2))

    # Log X-axis with clean bounds
    ax.set_xscale("log")
    if x_metric == "cost":
        ax.set_xlim(0.7, 16.0)
        ax.set_xticks([0.8, 1.0, 1.5, 2.0, 5.0, 7.0, 10.0, 15.0])
        ax.get_xaxis().set_major_formatter(ticker.FuncFormatter(
            lambda x, _: f"${x:.2f}" if x < 1 else f"${x:.1f}" if x in [1.5, 2.5, 5.05] else f"${x:.0f}"
        ))
    else:
        ax.set_xlim(700, 4500)
        ax.set_xticks([800, 1000, 1200, 1600, 2000, 3000, 4000])
        ax.get_xaxis().set_major_formatter(ticker.FuncFormatter(lambda x, _: f"{int(x)} ms"))

    # Keep the earlier visual cue, but make its status explicit. This is an
    # editorial guide for reading the plot, not a quality pass/fail cutoff.
    ax.axhspan(TARGET_QUALITY, 1.0, color=TARGET_BAND_COLOR, alpha=0.72, zorder=0)
    ax.axhline(TARGET_QUALITY, color=TARGET_LINE_COLOR, linestyle=":", linewidth=1.0, zorder=1)
    ax.text(
        0.025,
        0.955,
        f"Illustrative target quality region (nDCG@10 >= {TARGET_QUALITY:.2f})",
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=8,
        color=HEATMAP_MUTED,
        zorder=2,
    )

    # Cost bases are mixed, so any cost frontier is explicitly illustrative.
    if include_frontier:
        frontier = pareto_frontier_step(data, x_key, y_key)
        fxs = [p[x_key] for p in frontier]
        fys = [p[y_key] for p in frontier]
        ax.plot(fxs, fys, linestyle="--", color="#7f8c8d", linewidth=1.4, zorder=2, label="Illustrative point-estimate frontier")

    # Grid matching suite standard
    ax.grid(axis="both", alpha=0.3)
    
    # Label offsets
    offsets_cost = {
        "Parallel.ai": (6, 4),
        "Kagi": (6, -3),
        "Perplexity": (6, -10),
        "Context.dev": (6, -4),
        "Exa": (6, -10),
        "Octen": (6, 2)
    }
    
    offsets_latency = {
        "Parallel.ai": (6, 4),
        "Kagi": (-28, 6),
        "Perplexity": (-46, -10),
        "Context.dev": (6, -4),
        "Exa": (6, -10),
        "Octen": (6, 2)
    }

    offsets = offsets_cost if x_metric == "cost" else offsets_latency

    for p in data:
        xv = p[x_key]
        yv = p[y_key]
        name = p["display_name"]
        color = p["color"]
        
        # 95% CI error bar
        yerr_low = yv - p["ci_low"]
        yerr_high = p["ci_high"] - yv
        ax.errorbar(
            xv, yv, yerr=[[yerr_low], [yerr_high]],
            fmt="none", ecolor=color, elinewidth=1.2, capsize=3, capthick=1.2, zorder=3
        )
        
        # Provider dot
        ax.scatter(xv, yv, s=55, color=color, zorder=4)
        
        label = name
        dx, dy = offsets.get(name, (6, 4))
        ax.annotate(
            label,
            (xv, yv),
            xytext=(dx, dy),
            textcoords="offset points",
            fontsize=8 if x_metric == "cost" else 9,
            color=HEATMAP_TEXT,
            zorder=5
        )

    ax.set_xlabel(x_label)
    ax.set_ylabel(y_axis_label)
    ax.set_title(title)

    if include_frontier:
        ax.legend(loc="lower right", frameon=False)

    fig.text(0.01, 0.01, footer, fontsize=FOOTER_SIZE, color=HEATMAP_MUTED)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    
    plt.savefig(out_path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Generated clean benchmark Pareto chart at {out_path}")


def load_ai_quality(data: list, summary_path: Path) -> list:
    summary = pd.read_csv(summary_path)
    required = {"provider", "mean_ndcg10", "ci_low", "ci_high"}
    missing = required - set(summary.columns)
    if missing:
        raise ValueError(f"{summary_path}: missing columns {sorted(missing)}")

    by_provider = summary.set_index("provider")
    missing_providers = [point["provider"] for point in data if point["provider"] not in by_provider.index]
    if missing_providers:
        raise ValueError(f"{summary_path}: missing providers {missing_providers}")

    ai_data = []
    for point in data:
        metrics = by_provider.loc[point["provider"]]
        ai_data.append({
            **point,
            "ndcg10": float(metrics["mean_ndcg10"]),
            "ci_low": float(metrics["ci_low"]),
            "ci_high": float(metrics["ci_high"]),
        })
    return ai_data


def create_ai_3d_chart(data: list, out_path: Path) -> None:
    """Render a supplementary 3D view of AI quality, latency, and cost."""
    fig = plt.figure(figsize=(11, 8))
    ax = fig.add_subplot(111, projection="3d")

    # Matplotlib 3D axes do not render native log scales reliably. Transform
    # the two horizontal coordinates explicitly, then restore readable ticks.
    latency_ticks = [800, 1000, 1200, 1600, 2000, 3000, 4000]
    cost_ticks = [0.8, 1.0, 1.5, 2.0, 5.0, 7.0, 10.0, 15.0]
    ax.set_xlim(np.log10(700), np.log10(4500))
    ax.set_ylim(np.log10(0.7), np.log10(16.0))
    ax.set_zlim(0.0, 1.0)
    ax.set_proj_type("ortho")

    label_offsets = {
        # These nearby points need a little separation in the fixed camera view.
        "parallel": (-0.06, -0.02, 0.015),
        "exa": (0.04, 0.04, 0.015),
    }

    for point in data:
        latency = np.log10(point["p95_ms"])
        cost = np.log10(point["cost_1k"])
        ax.scatter(
            latency,
            cost,
            point["ndcg10"],
            s=65,
            color=point["color"],
            depthshade=False,
            edgecolor="white",
            linewidth=0.7,
        )
        offset_x, offset_y, offset_z = label_offsets.get(point["provider"], (0.0, 0.0, 0.015))
        ax.text(
            latency + offset_x,
            cost + offset_y,
            point["ndcg10"] + offset_z,
            point["display_name"],
            fontsize=8,
            color=HEATMAP_TEXT,
        )

    ax.set_xticks(np.log10(latency_ticks))
    ax.set_xticklabels([f"{value} ms" for value in latency_ticks])
    ax.set_yticks(np.log10(cost_ticks))
    ax.set_yticklabels([
        f"${value:.2f}" if value < 1 else f"${value:.1f}" if value in [1.5, 2.5, 5.05] else f"${value:.0f}"
        for value in cost_ticks
    ])
    ax.set_xlabel("p95 latency (log scale)", labelpad=10)
    ax.set_ylabel("Cost per 1,000 queries (log scale)", labelpad=0)
    ax.set_zlabel("Mean AI-judged nDCG@10", labelpad=14)
    ax.set_title("Mean AI-judged nDCG@10, p95 latency, and cost", pad=16)
    ax.view_init(elev=24, azim=-58)
    ax.set_box_aspect((1.35, 1.0, 0.9))
    ax.grid(alpha=0.3)

    fig.text(
        0.01,
        0.01,
        "Supplementary 3D view. Height is mean AI-judged nDCG@10; cost bases are provider-specific. Use the 2D Pareto charts for exact trade-offs.",
        fontsize=FOOTER_SIZE,
        color=HEATMAP_MUTED,
    )
    fig.subplots_adjust(left=0.01, right=0.87, bottom=0.12, top=0.93)
    # The fixed camera projects the cube toward the right side of the canvas;
    # shift the complete axes left so the angled cost label has breathing room.
    ax.set_position([-0.09, 0.12, 0.86, 0.81])
    plt.savefig(out_path, dpi=180, bbox_inches="tight", pad_inches=0.08, facecolor="white")
    plt.close(fig)
    print(f"Generated AI quality-cost-latency 3D chart at {out_path}")

def main():
    data = [
        # provider, display_name, color, ndcg10, ci_low, ci_high, cost_1k ($/1k requests), p95_ms
        {"provider": "brave",       "display_name": DISPLAY_NAMES["brave"],       "color": BRAND_COLORS["brave"],       "ndcg10": 0.659, "ci_low": 0.617, "ci_high": 0.701, "cost_1k":  5.00, "p95_ms":  820},
        {"provider": "context",     "display_name": DISPLAY_NAMES["context"],     "color": BRAND_COLORS["context"],     "ndcg10": 0.569, "ci_low": 0.533, "ci_high": 0.604, "cost_1k":  1.50, "p95_ms": 3854},
        {"provider": "exa",         "display_name": DISPLAY_NAMES["exa"],         "color": BRAND_COLORS["exa"],         "ndcg10": 0.523, "ci_low": 0.468, "ci_high": 0.577, "cost_1k":  7.00, "p95_ms": 1792},
        {"provider": "kagi","display_name": DISPLAY_NAMES["kagi"],"color": BRAND_COLORS["kagi"],"ndcg10": 0.634, "ci_low": 0.599, "ci_high": 0.670, "cost_1k": 12.00, "p95_ms": 1640},
        {"provider": "octen",       "display_name": DISPLAY_NAMES["octen"],       "color": BRAND_COLORS["octen"],       "ndcg10": 0.496, "ci_low": 0.453, "ci_high": 0.540, "cost_1k":  1.00, "p95_ms":  941},
        {"provider": "parallel",    "display_name": DISPLAY_NAMES["parallel"],    "color": BRAND_COLORS["parallel"],    "ndcg10": 0.675, "ci_low": 0.636, "ci_high": 0.713, "cost_1k":  5.05, "p95_ms": 1755},
        {"provider": "perplexity",  "display_name": DISPLAY_NAMES["perplexity"],  "color": BRAND_COLORS["perplexity"],  "ndcg10": 0.577, "ci_low": 0.537, "ci_high": 0.617, "cost_1k":  5.05, "p95_ms": 1224},
        {"provider": "tavily",      "display_name": DISPLAY_NAMES["tavily"],      "color": BRAND_COLORS["tavily"],      "ndcg10": 0.557, "ci_low": 0.506, "ci_high": 0.602, "cost_1k":  8.00, "p95_ms": 2564},
    ]

    run_dir = Path("/home/ubuntu/workspace/web-search/runs/rep-1-20260808-all8")
    out_dir = run_dir / "quality/study-charts"
    ai_out_dir = run_dir / "quality/charts-primary-ai"
    out_dir.mkdir(parents=True, exist_ok=True)
    ai_out_dir.mkdir(parents=True, exist_ok=True)

    create_pareto_chart(
        data,
        "cost",
        out_dir / "quality-cost-pareto.png",
        "Metadata-heuristic nDCG@10",
        "Mean nDCG@10",
        include_frontier=False,
    )
    create_pareto_chart(
        data,
        "latency",
        out_dir / "quality-latency-pareto.png",
        "Metadata-heuristic nDCG@10",
        "Mean nDCG@10",
        include_frontier=True,
    )

    ai_data = load_ai_quality(data, run_dir / "quality/charts-primary-ai/summary_metrics.csv")
    create_pareto_chart(
        ai_data,
        "cost",
        ai_out_dir / "quality-cost-pareto-ai.png",
        "Mean AI-judged nDCG@10",
        "Mean AI-judged nDCG@10",
        include_frontier=True,
    )
    create_pareto_chart(
        ai_data,
        "latency",
        ai_out_dir / "quality-latency-pareto-ai.png",
        "Mean AI-judged nDCG@10",
        "Mean AI-judged nDCG@10",
        include_frontier=True,
    )
    create_ai_3d_chart(ai_data, ai_out_dir / "quality-ai-cost-latency-3d.png")


if __name__ == "__main__":
    main()
