"""Shared visual constants and helpers for benchmark charts."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Sequence

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import Colormap, Normalize


PROVIDER_ORDER = ["context", "exa", "kagi_session", "octen", "parallel", "perplexity"]
DISPLAY_NAMES = {
    "context": "Context.dev",
    "exa": "Exa",
    "kagi_session": "Kagi",
    "octen": "Octen",
    "parallel": "Parallel.ai",
    "perplexity": "Perplexity",
}

# These are representative brand colors, not score colors. Use them for
# provider data marks such as dots, bars, and lines. Labels stay neutral so
# they do not compete with the chart's metric encoding.
BRAND_COLORS = {
    "context": "#5E6AD2",  # Context site indigo approximation
    "exa": "#1840ED",  # Exa official brand blue
    "kagi_session": "#FFB319",  # Kagi official yellow
    "octen": "#679987",  # Octen site accent
    "parallel": "#FB631B",  # Parallel site accent
    "perplexity": "#1FB8CD",  # Perplexity True Turquoise
}

CATEGORY_ORDER = [
    "known_item",
    "primary_source",
    "technical_exact",
    "semantic_long_tail",
    "niche_small_web",
    "multi_constraint",
    "exploratory_diversity",
    "multilingual_regional",
    "robustness_noisy",
    "freshness_rolling",
]
CATEGORY_LABELS = {
    "known_item": "Known item",
    "primary_source": "Primary source",
    "technical_exact": "Technical exact",
    "semantic_long_tail": "Semantic long tail",
    "niche_small_web": "Niche small web",
    "multi_constraint": "Multi-constraint",
    "exploratory_diversity": "Exploratory diversity",
    "multilingual_regional": "Multilingual regional",
    "robustness_noisy": "Robustness noisy",
    "freshness_rolling": "Freshness rolling",
}

HEATMAP_FIGSIZE = (14.0, 7.5)
HEATMAP_DPI = 180
HEATMAP_TEXT = "#172027"
HEATMAP_MUTED = "#5c6870"
HEATMAP_FOOTER_Y = 0.025
HEATMAP_TITLE_SIZE = 14
HEATMAP_TICK_SIZE = 9
HEATMAP_CELL_TEXT_SIZE = 8
HEATMAP_AXIS_LABEL_SIZE = 10
HEATMAP_COLORBAR_TICK_SIZE = 9
HEATMAP_FOOTER_SIZE = 8

# A ten-column, six-row heatmap occupies 74% by 63% of the fixed canvas.
# Deriving the axes size from these per-cell values prevents smaller matrices
# from stretching their cells across the full category-heatmap width.
HEATMAP_CELL_WIDTH = 0.074
HEATMAP_CELL_HEIGHT = 0.105
HEATMAP_GRID_CENTER_X = 0.48
HEATMAP_GRID_BOTTOM = 0.22
HEATMAP_COLORBAR_GAP = 0.018
HEATMAP_COLORBAR_WIDTH = 0.014


def ordered_values(values: Iterable[object], preferred: Sequence[str]) -> list[str]:
    """Return known values in the declared order, then unknown values sorted."""

    present = {str(value) for value in values if value is not None and str(value)}
    ordered = [value for value in preferred if value in present]
    ordered.extend(sorted(present - set(ordered)))
    return ordered


def provider_order(values: Iterable[object]) -> list[str]:
    return ordered_values(values, PROVIDER_ORDER)


def category_order(values: Iterable[object]) -> list[str]:
    return ordered_values(values, CATEGORY_ORDER)


def provider_labels(providers: Sequence[str]) -> list[str]:
    return [DISPLAY_NAMES.get(provider, provider) for provider in providers]


def style_heatmap_axes(ax: plt.Axes, providers: Sequence[str], categories: Sequence[str]) -> None:
    """Apply the shared heatmap geometry and provider-label treatment."""

    ax.set_xticks(
        np.arange(len(categories)),
        [CATEGORY_LABELS.get(category, category) for category in categories],
        rotation=35,
        ha="right",
    )
    ax.set_yticks(np.arange(len(providers)), provider_labels(providers))
    ax.tick_params(axis="both", which="major", length=0, labelsize=HEATMAP_TICK_SIZE)
    ax.set_xticks(np.arange(-0.5, len(categories), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(providers), 1), minor=True)
    ax.grid(which="minor", color="#ffffff", linewidth=0.6, alpha=0.55)
    ax.tick_params(axis="both", which="minor", length=0)
    for tick in ax.get_yticklabels():
        tick.set_color(HEATMAP_TEXT)
        tick.set_fontweight("normal")
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_facecolor("#f5f6f3")


def style_provider_matrix_axes(ax: plt.Axes, providers: Sequence[str]) -> None:
    """Apply the shared heatmap geometry to a provider-by-provider matrix."""

    labels = provider_labels(providers)
    ax.set_xticks(np.arange(len(providers)), labels, rotation=35, ha="right")
    ax.set_yticks(np.arange(len(providers)), labels)
    ax.tick_params(axis="both", which="major", length=0, labelsize=HEATMAP_TICK_SIZE)
    ax.set_xticks(np.arange(-0.5, len(providers), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(providers), 1), minor=True)
    ax.grid(which="minor", color="#ffffff", linewidth=0.6, alpha=0.55)
    ax.tick_params(axis="both", which="minor", length=0)
    for tick in [*ax.get_xticklabels(), *ax.get_yticklabels()]:
        tick.set_color(HEATMAP_TEXT)
        tick.set_fontweight("normal")
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_facecolor("#f5f6f3")


def annotation_color(value: float, vmin: float, vmax: float, cmap: Colormap) -> str:
    """Choose readable annotation text from the actual cell color."""

    rgba = cmap(Normalize(vmin=vmin, vmax=vmax, clip=True)(value))
    luminance = 0.2126 * rgba[0] + 0.7152 * rgba[1] + 0.0722 * rgba[2]
    return "#ffffff" if luminance < 0.52 else HEATMAP_TEXT


def annotate_heatmap(
    ax: plt.Axes,
    matrix: np.ndarray,
    *,
    cmap: Colormap,
    vmin: float,
    vmax: float,
    formatter: Callable[[float, int, int], str],
) -> None:
    for row in range(matrix.shape[0]):
        for column in range(matrix.shape[1]):
            value = matrix[row, column]
            if np.isnan(value):
                continue
            ax.text(
                column,
                row,
                formatter(float(value), row, column),
                ha="center",
                va="center",
                fontsize=HEATMAP_CELL_TEXT_SIZE,
                linespacing=1.0,
                color=annotation_color(float(value), vmin, vmax, cmap),
            )


def finish_heatmap(
    fig: plt.Figure,
    ax: plt.Axes,
    image,
    output: Path,
    *,
    colorbar_label: str,
    xlabel: str = "",
    footer: str = "",
) -> None:
    """Finish every heatmap with the same canvas, margins, and footer."""

    rows, columns = image.get_array().shape[:2]
    grid_width = HEATMAP_CELL_WIDTH * columns
    grid_height = HEATMAP_CELL_HEIGHT * rows
    grid_left = HEATMAP_GRID_CENTER_X - grid_width / 2
    ax.set_position([grid_left, HEATMAP_GRID_BOTTOM, grid_width, grid_height])
    colorbar_axis = fig.add_axes([
        grid_left + grid_width + HEATMAP_COLORBAR_GAP,
        HEATMAP_GRID_BOTTOM,
        HEATMAP_COLORBAR_WIDTH,
        grid_height,
    ])
    colorbar = fig.colorbar(image, cax=colorbar_axis)
    colorbar.set_label(colorbar_label, fontsize=HEATMAP_AXIS_LABEL_SIZE)
    colorbar.ax.tick_params(labelsize=HEATMAP_COLORBAR_TICK_SIZE)
    ax.title.set_fontsize(HEATMAP_TITLE_SIZE)
    ax.title.set_fontweight("normal")
    if xlabel:
        ax.set_xlabel(xlabel, labelpad=12, fontsize=HEATMAP_AXIS_LABEL_SIZE)
    if footer:
        fig.text(0.10, HEATMAP_FOOTER_Y, footer, fontsize=HEATMAP_FOOTER_SIZE, color=HEATMAP_MUTED)
    fig.savefig(output, dpi=HEATMAP_DPI, facecolor="white")
    plt.close(fig)
