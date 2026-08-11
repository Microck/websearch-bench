#!/usr/bin/env python3
"""Render the AI-versus-heuristic method-delta heatmap."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt

try:
    from chart_style import (
        CATEGORY_ORDER,
        HEATMAP_FIGSIZE,
        annotate_heatmap,
        finish_heatmap,
        provider_order,
        style_heatmap_axes,
    )
except ImportError:  # pragma: no cover - supports package-style imports
    from .chart_style import (
        CATEGORY_ORDER,
        HEATMAP_FIGSIZE,
        annotate_heatmap,
        finish_heatmap,
        provider_order,
        style_heatmap_axes,
    )


def make_heatmap(input_path: Path, output_path: Path) -> None:
    frame = pd.read_csv(input_path)
    required = {"provider", "category", "delta_ai_minus_heuristic"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise SystemExit(f"method delta file is missing columns: {', '.join(missing)}")

    providers = provider_order(frame["provider"])
    matrix = frame.pivot(index="provider", columns="category", values="delta_ai_minus_heuristic").reindex(
        index=providers,
        columns=CATEGORY_ORDER,
    )
    values = matrix.to_numpy(dtype=float)
    limit = float(np.nanmax(np.abs(values))) if np.isfinite(values).any() else 1.0
    limit = max(limit, 0.01)

    fig, ax = plt.subplots(figsize=HEATMAP_FIGSIZE)
    cmap = plt.get_cmap("RdBu_r")
    image = ax.imshow(values, cmap=cmap, aspect="auto", vmin=-limit, vmax=limit)
    style_heatmap_axes(ax, providers, CATEGORY_ORDER)
    ax.set_title("Method delta - AI judge minus metadata heuristic", pad=8)
    annotate_heatmap(
        ax,
        values,
        cmap=cmap,
        vmin=-limit,
        vmax=limit,
        formatter=lambda value, _row, _column: f"{value:+.2f}",
    )
    finish_heatmap(
        fig,
        ax,
        image,
        output_path,
        colorbar_label="AI nDCG@10 minus heuristic nDCG@10",
        footer="Positive cells mean the AI judge scored that provider/category higher. Deltas use unrounded category means; cells are shown to two decimals.",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    make_heatmap(args.input, args.output)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
