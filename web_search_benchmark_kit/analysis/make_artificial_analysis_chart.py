#!/usr/bin/env python3
"""Generate quality-versus-cost and quality-versus-latency scatter charts."""

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
from pathlib import Path
try:
    from chart_style import BRAND_COLORS, DISPLAY_NAMES, HEATMAP_MUTED, HEATMAP_TEXT
except ImportError:  # pragma: no cover - supports package-style imports
    from .chart_style import BRAND_COLORS, DISPLAY_NAMES, HEATMAP_MUTED, HEATMAP_TEXT

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
    out_path: Path
):
    fig, ax = plt.subplots(figsize=(9, 5.5))
    
    x_key = "cost_1k" if x_metric == "cost" else "p95_ms"
    y_key = "ndcg10"
    
    if x_metric == "cost":
        title = "Provisional metadata-heuristic nDCG@10 versus cost"
        x_label = "Cost per 1,000 queries (USD, log scale)"
        footer = "Cost bases are provider-specific. Shaded band and target line are illustrative. Whiskers = paired-bootstrap 95% CI."
    else:
        title = "Provisional metadata-heuristic nDCG@10 versus p95 latency"
        x_label = "p95 latency (ms, log scale)"
        footer = "p95 is HTTP latency. Shaded band and target line are illustrative. Dashed line = point-estimate frontier; whiskers = paired-bootstrap 95% CI."

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

    # Only the latency view receives an illustrative point-estimate frontier.
    # Cost points use mixed bases, so a frontier would imply false comparability.
    if x_metric == "latency":
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
    ax.set_ylabel("Mean nDCG@10")
    ax.set_title(title)

    if x_metric == "latency":
        ax.legend(loc="lower right", frameon=False)

    fig.text(0.01, 0.01, footer, fontsize=8, color=HEATMAP_MUTED)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    
    plt.savefig(out_path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Generated clean benchmark Pareto chart at {out_path}")

def main():
    data = [
        {"provider": "octen", "display_name": DISPLAY_NAMES["octen"], "color": BRAND_COLORS["octen"], "ndcg10": 0.509, "ci_low": 0.466, "ci_high": 0.553, "cost_1k": 1.00, "p95_ms": 941},
        {"provider": "context", "display_name": DISPLAY_NAMES["context"], "color": BRAND_COLORS["context"], "ndcg10": 0.543, "ci_low": 0.503, "ci_high": 0.582, "cost_1k": 1.50, "p95_ms": 3854},
        {"provider": "perplexity", "display_name": DISPLAY_NAMES["perplexity"], "color": BRAND_COLORS["perplexity"], "ndcg10": 0.569, "ci_low": 0.526, "ci_high": 0.611, "cost_1k": 5.0515, "p95_ms": 1224},
        {"provider": "exa", "display_name": DISPLAY_NAMES["exa"], "color": BRAND_COLORS["exa"], "ndcg10": 0.519, "ci_low": 0.460, "ci_high": 0.575, "cost_1k": 7.00, "p95_ms": 1792},
        {"provider": "kagi_session", "display_name": DISPLAY_NAMES["kagi_session"], "color": BRAND_COLORS["kagi_session"], "ndcg10": 0.624, "ci_low": 0.586, "ci_high": 0.661, "cost_1k": 12.00, "p95_ms": 1640},
        {"provider": "parallel", "display_name": DISPLAY_NAMES["parallel"], "color": BRAND_COLORS["parallel"], "ndcg10": 0.687, "ci_low": 0.650, "ci_high": 0.723, "cost_1k": 5.0515, "p95_ms": 1755},
    ]

    out_dir = Path("/home/ubuntu/workspace/web-search/runs/rep-1-20260805-combined/report-site/assets")
    out_dir.mkdir(parents=True, exist_ok=True)

    create_pareto_chart(data, "cost", out_dir / "quality-cost-pareto.png")
    create_pareto_chart(data, "latency", out_dir / "quality-latency-pareto.png")

if __name__ == "__main__":
    main()
