#!/usr/bin/env python3
"""Render final URL health as one normalized share bar per provider."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    from chart_style import DISPLAY_NAMES, provider_order
except ImportError:  # pragma: no cover - supports package-style imports
    from .chart_style import DISPLAY_NAMES, provider_order


ORDER = [
    "confirmed_get_ok",
    "confirmed_get_redirect",
    "reachable_tls_warning",
    "reachable_alternate_network",
    "access_or_rate_limit_warning",
    "server_or_network_warning",
    "likely_dead_not_found",
]
LABELS = {
    "confirmed_get_ok": "GET OK",
    "confirmed_get_redirect": "GET redirect",
    "reachable_tls_warning": "TLS-only diagnostic",
    "reachable_alternate_network": "Alternate-network diagnostic",
    "access_or_rate_limit_warning": "Access/rate warning",
    "server_or_network_warning": "Server/network warning",
    "likely_dead_not_found": "404/410 likely dead",
}


def add_historical_subtitle(input_path: Path, output_path: Path, subtitle: str) -> None:
    """Add an explicit historical scope band without changing the chart data."""

    image = Image.open(input_path).convert("RGB")
    band_height = 72
    canvas = Image.new("RGB", (image.width, image.height + band_height), "white")
    canvas.paste(image, (0, 0))
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
    except OSError:
        font = ImageFont.load_default()
    draw.text((24, image.height + 20), subtitle, fill="#5c6870", font=font)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)


def render_historical_targeted_chart(input_path: Path, output_path: Path, *, title: str, subtitle: str) -> None:
    """Render an old targeted summary with an external legend and explicit scope."""

    frame = pd.read_csv(input_path).fillna(0)
    statuses = [
        ("ok", "GET OK", "#197c5a", ""),
        ("ok_redirect", "GET redirect", "#2f9b77", ""),
        ("access_or_rate_limit_warning", "Access/rate warning", "#d95f59", ".."),
        ("server_or_network_warning", "Server/network warning", "#bb4a4c", "xx"),
        ("likely_dead_not_found", "404/410 likely dead", "#68758a", "++"),
        ("other_broken", "Other HTTP error", "#8b5b8a", "//"),
        ("unresolved_missing", "Unresolved/missing", "#9aa4aa", "\\\\"),
    ]
    labels = frame["first_classification"].astype(str).str.replace("_", " ").str.title().tolist()
    fig, axis = plt.subplots(figsize=(12, 6.5))
    x = np.arange(len(frame))
    bottom = np.zeros(len(frame))
    for key, label, color, hatch in statuses:
        values = pd.to_numeric(frame.get(key, 0), errors="coerce").fillna(0).to_numpy(dtype=float)
        axis.bar(x, values, bottom=bottom, label=label, color=color, hatch=hatch, edgecolor="white", linewidth=0.5)
        bottom += values
    axis.set_xticks(x, labels, rotation=18, ha="right")
    axis.set_ylabel("URLs in first-pass category")
    axis.set_title(title, pad=12)
    axis.grid(axis="y", alpha=0.2)
    axis.set_axisbelow(True)
    handles, legend_labels = axis.get_legend_handles_labels()
    fig.legend(handles, legend_labels, loc="upper center", bbox_to_anchor=(0.5, 0.90), ncol=3, frameon=False)
    fig.text(0.5, 0.02, subtitle, ha="center", fontsize=8, color="#5c6870")
    fig.subplots_adjust(top=0.70, bottom=0.22, left=0.08, right=0.98)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def render(input_path: Path, output_path: Path) -> None:
    frame = pd.read_csv(input_path)
    required = {
        "provider",
        "confirmed_get_ok",
        "confirmed_get_redirect",
        "reachable_tls_warning",
        "reachable_alternate_network",
        "access_or_rate_limit_warning",
        "server_or_network_warning",
        "likely_dead_not_found",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise SystemExit(f"{input_path} is missing columns: {', '.join(missing)}")

    for column in ORDER:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    providers = provider_order(frame["provider"])
    frame = frame.set_index("provider").reindex(providers).fillna(0).reset_index()
    totals = frame[ORDER].sum(axis=1)
    shares = frame[ORDER].div(totals.replace(0, 1), axis=0) * 100

    status_colors = {
        "confirmed_get_ok": "#197c5a",
        "confirmed_get_redirect": "#2f9b77",
        "reachable_tls_warning": "#d6a84f",
        "reachable_alternate_network": "#c08b2d",
        "access_or_rate_limit_warning": "#d95f59",
        "server_or_network_warning": "#bb4a4c",
        "likely_dead_not_found": "#68758a",
    }
    status_hatches = {
        "confirmed_get_ok": "",
        "confirmed_get_redirect": "",
        "reachable_tls_warning": "//",
        "reachable_alternate_network": "\\\\",
        "access_or_rate_limit_warning": "..",
        "server_or_network_warning": "xx",
        "likely_dead_not_found": "++",
    }
    fig, axis = plt.subplots(figsize=(12, 6.5))
    y = range(len(providers))
    left = pd.Series(0.0, index=frame.index)
    for classification in ORDER:
        values = shares[classification]
        axis.barh(
            y,
            values,
            left=left,
            color=status_colors[classification],
            label=LABELS[classification],
            hatch=status_hatches[classification],
            edgecolor="white",
            linewidth=0.5,
        )
        left += values

    axis.set_yticks(list(y), [DISPLAY_NAMES.get(provider, provider) for provider in providers])
    for tick in axis.get_yticklabels():
        tick.set_color("#172027")
        tick.set_fontweight("normal")
    axis.invert_yaxis()
    axis.set_title("Final HTTP reachability outcomes by provider")
    axis.set_xlabel("Share of each provider's normalized URLs (%)")
    axis.grid(axis="x", alpha=0.2)
    axis.set_axisbelow(True)
    axis.set_xlim(0, 145)
    axis.set_xticks(range(0, 101, 20))
    for row, provider in enumerate(providers):
        confirmed = frame.loc[row, "confirmed_get_ok"] + frame.loc[row, "confirmed_get_redirect"]
        axis.text(
            102,
            row,
            f"n={int(totals.iloc[row]):,} | {100 * confirmed / max(1, totals.iloc[row]):.1f}% clean GET/redirect",
            va="center",
            fontsize=8,
            color="#5c6870",
        )
    axis.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=3, frameon=False)
    fig.text(
        0.01,
        0.01,
        "Provider-local normalized URL counts. Clean share is GET OK or GET redirect. Source-level confirmations are not shown; warning categories are unconfirmed, not dead. Pass 5 rechecked 138 residual rows and promoted none.",
        color="#5c6870",
        fontsize=8,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    render(args.input, args.output)
    print(args.output)


if __name__ == "__main__":
    main()
