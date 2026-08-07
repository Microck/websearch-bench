#!/usr/bin/env python3
"""Generate raw-study diagnostics for one benchmark repetition.

The charts in this script use request logs, retrieved result rows, URL
resolution, query categories, and billing estimates. They deliberately do
not read relevance labels or AI-judging artifacts.
"""

from __future__ import annotations

import argparse
from html import escape as html_escape
from pathlib import Path
from urllib.parse import urlparse

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt

try:
    from chart_style import (
        BRAND_COLORS,
        CATEGORY_LABELS,
        CATEGORY_ORDER,
        DISPLAY_NAMES,
        HEATMAP_FIGSIZE,
        annotate_heatmap,
        finish_heatmap,
        provider_order as shared_provider_order,
        style_heatmap_axes,
        style_provider_matrix_axes,
    )
except ImportError:  # pragma: no cover - supports package-style imports
    from .chart_style import (
        BRAND_COLORS,
        CATEGORY_LABELS,
        CATEGORY_ORDER,
        DISPLAY_NAMES,
        HEATMAP_FIGSIZE,
        annotate_heatmap,
        finish_heatmap,
        provider_order as shared_provider_order,
        style_heatmap_axes,
        style_provider_matrix_axes,
    )


COLORS = BRAND_COLORS
KIT_ROOT = Path(__file__).resolve().parents[1]


def require_columns(frame: pd.DataFrame, name: str, columns: set[str]) -> None:
    missing = sorted(columns - set(frame.columns))
    if missing:
        raise SystemExit(f"{name} is missing columns: {', '.join(missing)}")


def provider_order(data: dict[str, pd.DataFrame]) -> list[str]:
    return shared_provider_order(data["runs"]["provider"])


def load_inputs(run_dir: Path) -> dict[str, pd.DataFrame]:
    run_dir = run_dir.resolve()
    inputs = {
        "runs": pd.read_csv(run_dir / "runs.csv"),
        "results": pd.read_csv(run_dir / "retrieved_results.csv"),
        "queries": pd.read_csv(KIT_ROOT / "data" / "queries.csv"),
        "resolution": pd.read_csv(run_dir / "url_resolution.csv"),
        "summary": pd.read_csv(run_dir / "run_summary.csv"),
        "billing": pd.read_csv(run_dir / "provider_billing_estimates.csv"),
    }
    require_columns(inputs["runs"], "runs.csv", {"query_id", "query_variant", "provider", "latency_ms", "http_status", "error_type"})
    require_columns(inputs["results"], "retrieved_results.csv", {"query_id", "query_variant", "provider", "rank", "canonical_url", "url"})
    require_columns(inputs["queries"], "queries.csv", {"query_id", "category"})
    require_columns(inputs["resolution"], "url_resolution.csv", {"original_url", "http_status", "redirected", "broken"})
    require_columns(inputs["summary"], "run_summary.csv", {"provider", "total_cost_usd_reported"})
    require_columns(inputs["billing"], "provider_billing_estimates.csv", {"scope", "provider", "estimated_cost_usd", "rate_basis"})
    return inputs


def save_figure(fig: plt.Figure, path: Path) -> None:
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def category_frame(queries: pd.DataFrame) -> pd.DataFrame:
    result = queries[["query_id", "category"]].drop_duplicates()
    result["category"] = pd.Categorical(result["category"], CATEGORY_ORDER, ordered=True)
    return result


def make_latency_heatmap(data: dict[str, pd.DataFrame], out_dir: Path) -> None:
    runs = data["runs"].copy()
    providers = provider_order(data)
    queries = category_frame(data["queries"])
    runs = runs[runs["query_variant"].eq("primary")].merge(queries, on="query_id", how="inner")
    runs["latency_ms"] = pd.to_numeric(runs["latency_ms"], errors="coerce")
    runs["http_status"] = pd.to_numeric(runs["http_status"], errors="coerce")
    successful = runs[runs["http_status"].between(200, 299) & runs["error_type"].fillna("").eq("") & runs["latency_ms"].gt(0)]
    grouped = successful.groupby(["provider", "category"], observed=False)["latency_ms"].agg(
        requests="count",
        p50=lambda values: float(np.percentile(values, 50)),
        p95=lambda values: float(np.percentile(values, 95)),
        mean="mean",
    ).reset_index()
    grouped.to_csv(out_dir / "category-latency.csv", index=False)
    grouped = grouped.set_index(["provider", "category"]).reindex(
        pd.MultiIndex.from_product([providers, CATEGORY_ORDER], names=["provider", "category"])
    ).reset_index()
    matrix = grouped.pivot(index="provider", columns="category", values="p50").reindex(index=providers, columns=CATEGORY_ORDER)
    values = matrix.to_numpy(dtype=float)
    p95 = grouped.pivot(index="provider", columns="category", values="p95").reindex(index=providers, columns=CATEGORY_ORDER)
    vmin = 0.0
    vmax = float(np.nanmax(values)) if np.isfinite(values).any() else 1.0
    fig, ax = plt.subplots(figsize=HEATMAP_FIGSIZE)
    cmap = plt.get_cmap("YlGnBu")
    image = ax.imshow(values, cmap=cmap, aspect="auto", vmin=vmin, vmax=vmax)
    style_heatmap_axes(ax, providers, CATEGORY_ORDER)
    ax.set_title("Primary-query latency by category", pad=16)
    annotate_heatmap(
        ax,
        values,
        cmap=cmap,
        vmin=vmin,
        vmax=vmax,
        formatter=lambda value, row, column: f"{value:,.0f}\n({p95.iat[row, column]:,.0f})",
    )
    finish_heatmap(
        fig,
        ax,
        image,
        out_dir / "category-latency-heatmap.png",
        colorbar_label="Median latency (ms)",
        xlabel="Each cell shows median ms; the second line is p95 ms",
        footer="Eight primary queries per category and provider. This is a workload profile, not a general SLO.",
    )


def make_completeness_heatmap(data: dict[str, pd.DataFrame], out_dir: Path) -> None:
    results = data["results"]
    providers = provider_order(data)
    queries = category_frame(data["queries"])
    primary_queries = queries["query_id"].tolist()
    request_grid = pd.MultiIndex.from_product([primary_queries, providers], names=["query_id", "provider"]).to_frame(index=False)
    observed = results[results["query_variant"].eq("primary")].groupby(["query_id", "provider"], as_index=False).agg(result_count=("rank", "count"))
    observed = request_grid.merge(observed, on=["query_id", "provider"], how="left").fillna({"result_count": 0})
    observed = observed.merge(queries, on="query_id", how="left")
    observed["short_list"] = observed["result_count"] < 10
    grouped = observed.groupby(["provider", "category"], observed=False).agg(
        requests=("query_id", "count"),
        short_list_pct=("short_list", lambda values: 100 * float(values.mean())),
        mean_results=("result_count", "mean"),
        full_ten_pct=("result_count", lambda values: 100 * float((values == 10).mean())),
    ).reset_index()
    grouped.to_csv(out_dir / "category-completeness.csv", index=False)
    grouped = grouped.set_index(["provider", "category"]).reindex(
        pd.MultiIndex.from_product([providers, CATEGORY_ORDER], names=["provider", "category"])
    ).reset_index()
    matrix = grouped.pivot(index="provider", columns="category", values="short_list_pct").reindex(index=providers, columns=CATEGORY_ORDER)
    means = grouped.pivot(index="provider", columns="category", values="mean_results").reindex(index=providers, columns=CATEGORY_ORDER)
    values = matrix.to_numpy(dtype=float)
    fig, ax = plt.subplots(figsize=HEATMAP_FIGSIZE)
    cmap = plt.get_cmap("YlOrRd")
    image = ax.imshow(values, cmap=cmap, aspect="auto", vmin=0, vmax=100)
    style_heatmap_axes(ax, providers, CATEGORY_ORDER)
    ax.set_title("Short-list rate by primary-query category", pad=16)
    annotate_heatmap(
        ax,
        values,
        cmap=cmap,
        vmin=0,
        vmax=100,
        formatter=lambda value, row, column: f"{value:.0f}%\n({means.iat[row, column]:.2f})",
    )
    finish_heatmap(
        fig,
        ax,
        image,
        out_dir / "category-completeness-heatmap.png",
        colorbar_label="Requests returning fewer than 10 results (%)",
        xlabel="Each cell shows short lists (<10 results); the second line is mean result count",
        footer="Observed ranked rows per request. This measures list completeness, not relevance.",
    )


def make_agreement_matrix(data: dict[str, pd.DataFrame], out_dir: Path) -> None:
    results = data["results"]
    providers = provider_order(data)
    primary = results[results["query_variant"].eq("primary") & pd.to_numeric(results["rank"], errors="coerce").le(10)].copy()
    primary["result_key"] = primary["canonical_url"].where(primary["canonical_url"].fillna("").ne(""), primary["url"])
    sets = {(query_id, provider): set(group["result_key"].dropna()) for (query_id, provider), group in primary.groupby(["query_id", "provider"])}
    query_ids = data["queries"]["query_id"].tolist()
    matrix = pd.DataFrame(index=providers, columns=providers, dtype=float)
    rows: list[dict[str, object]] = []
    for left in providers:
        for right in providers:
            values = []
            for query_id in query_ids:
                left_set = sets.get((query_id, left), set())
                right_set = sets.get((query_id, right), set())
                union = left_set | right_set
                values.append(len(left_set & right_set) / len(union) if union else 0.0)
            matrix.loc[left, right] = float(np.mean(values))
            rows.append({
                "provider_a": left,
                "provider_b": right,
                "queries": len(values),
                "mean_jaccard_at_10": float(np.mean(values)),
                "median_jaccard_at_10": float(np.median(values)),
                "q1_jaccard_at_10": float(np.percentile(values, 25)),
                "q3_jaccard_at_10": float(np.percentile(values, 75)),
            })
    pd.DataFrame(rows).to_csv(out_dir / "raw-agreement.csv", index=False)
    values = matrix.to_numpy(dtype=float)
    display_values = values.copy()
    np.fill_diagonal(display_values, np.nan)
    off_diagonal = display_values[np.isfinite(display_values)]
    vmax = float(np.nanmax(off_diagonal)) if off_diagonal.size else 1.0
    vmax = max(vmax, 0.05)
    fig, ax = plt.subplots(figsize=HEATMAP_FIGSIZE)
    cmap = plt.get_cmap("Blues").copy()
    cmap.set_bad("#d9dfdd")
    image = ax.imshow(display_values, cmap=cmap, aspect="auto", vmin=0, vmax=vmax)
    style_provider_matrix_axes(ax, providers)
    ax.set_title("Raw result-set agreement", pad=16)
    annotate_heatmap(
        ax,
        display_values,
        cmap=cmap,
        vmin=0,
        vmax=vmax,
        formatter=lambda value, _row, _column: f"{value:.2f}",
    )
    for index in range(len(providers)):
        ax.text(index, index, "1.00", ha="center", va="center", fontsize=8, color="#172027")
    finish_heatmap(
        fig,
        ax,
        image,
        out_dir / "raw-result-agreement.png",
        colorbar_label="Mean off-diagonal overlap",
        xlabel="Mean Jaccard overlap of all canonical URLs in the top 10",
        footer="Diagonal cells are fixed self-overlap (1.00) and shown in gray. Off-diagonal agreement is descriptive, not a quality score.",
    )


def classify_resolution(joined: pd.DataFrame) -> pd.Series:
    status = pd.to_numeric(joined["http_status"], errors="coerce")
    broken = joined["broken"].fillna(0).eq(1) | status.ge(400)
    resolved = joined["final_url"].notna()
    reachable = resolved & ~broken & status.between(200, 399)
    redirected = reachable & joined["redirected"].fillna(0).eq(1)
    direct = reachable & ~redirected
    return pd.Series(
        np.select(
            [~resolved, broken, redirected, direct],
            ["unresolved", "resolver flagged", "redirected ok", "direct ok"],
            default="unresolved",
        ),
        index=joined.index,
    )


def make_resolution_health(data: dict[str, pd.DataFrame], out_dir: Path) -> None:
    results = data["results"]
    providers = provider_order(data)
    primary = results[results["query_variant"].eq("primary")].copy()
    resolution = data["resolution"][["original_url", "final_url", "http_status", "redirected", "broken"]].drop_duplicates("original_url")
    joined = primary.merge(resolution, left_on="canonical_url", right_on="original_url", how="left")
    joined["resolution_class"] = classify_resolution(joined)
    classes = ["direct ok", "redirected ok", "resolver flagged", "unresolved"]
    overall = joined.groupby(["provider", "resolution_class"], observed=False).size().rename("count").reset_index()
    overall = overall.set_index(["provider", "resolution_class"]).reindex(
        pd.MultiIndex.from_product([providers, classes], names=["provider", "resolution_class"]), fill_value=0
    ).reset_index()
    overall["pct"] = overall["count"] / overall.groupby("provider")["count"].transform("sum") * 100
    overall.to_csv(out_dir / "url-resolution-overall.csv", index=False)
    by_rank = joined.assign(rank=pd.to_numeric(joined["rank"], errors="coerce")).groupby(["provider", "rank"], observed=False).agg(
        slots=("resolution_class", "size"),
        reachable=("resolution_class", lambda values: 100 * float(values.isin(["direct ok", "redirected ok"]).mean())),
    ).reset_index()
    by_rank.to_csv(out_dir / "url-resolution-by-rank.csv", index=False)

    fig, axes = plt.subplots(1, 2, figsize=(14, 5.8), gridspec_kw={"width_ratios": [1.05, 1.35]})
    left = overall.pivot(index="provider", columns="resolution_class", values="pct").reindex(index=providers, columns=classes).fillna(0)
    bottom = np.zeros(len(providers))
    class_colors = {"direct ok": "#087a4b", "redirected ok": "#54a24b", "resolver flagged": "#d66a4c", "unresolved": "#a8b0b3"}
    for item in classes:
        values = left[item].to_numpy()
        axes[0].barh(range(len(providers)), values, left=bottom, label=item, color=class_colors[item])
        bottom += values
    axes[0].set_yticks(range(len(providers)), [DISPLAY_NAMES.get(item, item) for item in providers])
    axes[0].invert_yaxis()
    axes[0].set_xlim(0, 100)
    axes[0].set_xlabel("Share of primary result slots (%)")
    axes[0].set_title("URL resolver outcomes")
    axes[0].legend(fontsize=8, loc="lower left", bbox_to_anchor=(0, -0.34), ncol=2)
    axes[0].grid(axis="x", alpha=0.25)

    for index, provider in enumerate(providers):
        current = by_rank[by_rank["provider"].eq(provider)]
        axes[1].plot(
            current["rank"],
            current["reachable"],
            marker="o",
            linewidth=2,
            linestyle=("-", "--", "-.", ":")[index % 4],
            color=COLORS.get(provider, "#7f8c8d"),
            label=DISPLAY_NAMES.get(provider, provider),
        )
    axes[1].set_xticks(range(1, 11))
    axes[1].set_ylim(0, 105)
    axes[1].set_xlabel("Result rank")
    axes[1].set_ylabel("Resolver-observed success (%)")
    axes[1].set_title("Resolver-observed success by rank")
    axes[1].grid(alpha=0.25)
    axes[1].legend(fontsize=8)
    fig.text(0.01, 0.01, "Resolver snapshot: flagged does not prove unavailable. 403/429 can be access blocks; unresolved URLs were not tested.", fontsize=8, color="#5c6870")
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    save_figure(fig, out_dir / "url-resolution-health.png")


def flag_reason(status_value: object, error_value: object) -> str:
    status = int(float(status_value)) if pd.notna(status_value) else None
    error = str(error_value) if pd.notna(error_value) else ""
    if status in {403, 429}:
        return "access or rate-limit response"
    if status in {404, 410}:
        return "not-found response"
    if status is not None and 500 <= status < 600:
        return "server or gateway response"
    if status in {400, 401}:
        return "client or authentication response"
    if status is not None and 300 <= status < 400:
        return "redirect response flagged by resolver"
    if "CERTIFICATE_VERIFY_FAILED" in error or "TLS" in error or "ssl" in error.lower():
        return "TLS or certificate failure in resolver"
    if "timed out" in error.lower() or "timeout" in error.lower():
        return "resolver timeout"
    if error:
        return "network or resolver failure"
    return "unclassified resolver flag"


def join_unique(values: pd.Series) -> str:
    return "; ".join(sorted({str(value) for value in values if pd.notna(value) and str(value)}))


def make_flagged_inventory(data: dict[str, pd.DataFrame], out_dir: Path) -> None:
    resolution = data["resolution"].copy()
    flagged = resolution[resolution["broken"].fillna(0).eq(1)].copy()
    results = data["results"][["query_id", "query_variant", "category", "provider", "rank", "canonical_url"]]
    uses = results.merge(
        flagged[["original_url", "final_url", "http_status", "error"]],
        left_on="canonical_url",
        right_on="original_url",
        how="inner",
        validate="many_to_one",
    )
    rows: list[dict[str, object]] = []
    for original_url, group in uses.groupby("original_url", sort=True):
        first = group.iloc[0]
        rows.append(
            {
                "reason": flag_reason(first["http_status"], first["error"]),
                "http_status": "" if pd.isna(first["http_status"]) else int(float(first["http_status"])),
                "error": "" if pd.isna(first["error"]) else str(first["error"]),
                "original_url": original_url,
                "final_url": "" if pd.isna(first["final_url"]) else str(first["final_url"]),
                "providers": join_unique(group["provider"].map(DISPLAY_NAMES)),
                "query_variants": join_unique(group["query_variant"]),
                "query_ids": join_unique(group["query_id"]),
                "categories": join_unique(group["category"]),
                "ranks": join_unique(group["rank"]),
                "occurrences": len(group),
            }
        )
    inventory = pd.DataFrame(rows).sort_values(["reason", "http_status", "original_url"], na_position="last")
    inventory.to_csv(out_dir / "url-resolution-flagged.csv", index=False)

    summary = inventory.groupby("reason", as_index=False).size().rename(columns={"size": "unique_urls"})
    summary_rows = "".join(
        f"<tr><td>{html_escape(str(row.reason))}</td><td>{int(row.unique_urls)}</td></tr>"
        for row in summary.itertuples(index=False)
    )
    table_rows = []
    for index, row in enumerate(inventory.itertuples(index=False), start=1):
        original = html_escape(str(row.original_url), quote=True)
        final = html_escape(str(row.final_url), quote=True) if row.final_url else ""
        original_link = f'<a href="{original}" target="_blank" rel="noreferrer">open original</a>'
        final_link = f'<a href="{final}" target="_blank" rel="noreferrer">open final</a>' if final else ""
        table_rows.append(
            "<tr>"
            f"<td>{index}</td><td>{html_escape(str(row.reason))}</td>"
            f"<td>{html_escape(str(row.http_status)) if row.http_status else 'none'}</td>"
            f"<td>{original_link}<br><code>{original}</code><br>{final_link}</td>"
            f"<td>{html_escape(str(row.providers))}</td><td>{html_escape(str(row.query_ids))}</td>"
            f"<td>{html_escape(str(row.occurrences))}</td><td>{html_escape(str(row.error))}</td>"
            "</tr>"
        )
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Resolver-flagged URL inventory</title>
  <style>
    body {{ margin: 32px; color: #172027; background: #f4f5f2; font: 14px/1.45 system-ui, sans-serif; }}
    main {{ max-width: 1500px; margin: 0 auto; }}
    h1 {{ line-height: 1.1; }}
    .note {{ max-width: 1000px; color: #5c6870; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; }}
    th, td {{ padding: 8px 10px; border: 1px solid #c8d0d3; text-align: left; vertical-align: top; }}
    th {{ background: #e9eeed; position: sticky; top: 0; }}
    code {{ overflow-wrap: anywhere; color: #5c6870; }}
    a {{ color: #086f8c; }}
    .summary {{ width: auto; margin-bottom: 24px; }}
    .summary th, .summary td {{ padding: 6px 10px; }}
  </style>
</head>
<body>
<main>
  <h1>Resolver-flagged URL inventory</h1>
  <p class="note">This is the complete inventory of {len(inventory)} unique URLs that the benchmark resolver flagged on 2026-08-05. A flag is not proof that a page is unavailable. HTTP 403/429 responses may be access or rate-limit blocks, and TLS, timeout, and gateway errors may be local or transient. Open each link below to verify it yourself.</p>
  <table class="summary"><thead><tr><th>Resolver reason</th><th>Unique URLs</th></tr></thead><tbody>{summary_rows}</tbody></table>
  <table><thead><tr><th>#</th><th>Reason</th><th>Status</th><th>Links</th><th>Providers</th><th>Query IDs</th><th>Uses</th><th>Resolver error</th></tr></thead><tbody>{''.join(table_rows)}</tbody></table>
</main>
</body>
</html>
"""
    (out_dir / "url-resolution-flagged.html").write_text(document, encoding="utf-8")


def host_name(value: str) -> str:
    try:
        host = (urlparse(value).hostname or "").lower().removeprefix("www.")
    except ValueError:
        host = ""
    return host or "(unparsed URL)"


def make_source_ecology(data: dict[str, pd.DataFrame], out_dir: Path) -> None:
    results = data["results"]
    providers = provider_order(data)
    primary = results[results["query_variant"].eq("primary")].copy()
    primary["result_key"] = primary["canonical_url"].where(primary["canonical_url"].fillna("").ne(""), primary["url"])
    primary["host"] = primary["result_key"].map(host_name)
    counts = primary.groupby(["query_id", "provider", "host"], as_index=False).size().rename(columns={"size": "slots"})
    totals = counts.groupby(["query_id", "provider"], as_index=False)["slots"].sum().rename(columns={"slots": "total_slots"})
    counts = counts.merge(totals, on=["query_id", "provider"], how="left")
    counts["share"] = counts["slots"] / counts["total_slots"]
    top_domains = counts.groupby("host")["slots"].sum().nlargest(8).index.tolist()
    query_provider = pd.MultiIndex.from_product(
        [data["queries"]["query_id"].tolist(), providers], names=["query_id", "provider"]
    ).to_frame(index=False)
    top_grid = pd.MultiIndex.from_product(
        [data["queries"]["query_id"].tolist(), providers, top_domains],
        names=["query_id", "provider", "host"],
    ).to_frame(index=False)
    top_shares = top_grid.merge(counts[["query_id", "provider", "host", "share"]], on=["query_id", "provider", "host"], how="left").fillna({"share": 0})
    top_means = top_shares.groupby(["provider", "host"], as_index=False)["share"].mean()
    other = query_provider.merge(top_shares.groupby(["query_id", "provider"], as_index=False)["share"].sum(), on=["query_id", "provider"], how="left")
    other["host"] = "Other hosts"
    other["share"] = 1 - other["share"]
    other_means = other.groupby(["provider", "host"], as_index=False)["share"].mean()
    shares = pd.concat([top_means, other_means], ignore_index=True)
    pivot = shares.pivot(index="provider", columns="host", values="share").reindex(index=providers).fillna(0)
    ordered_domains = [domain for domain in top_domains if domain in pivot.columns] + ["Other hosts"]
    pivot = pivot.reindex(columns=ordered_domains, fill_value=0)
    pivot.to_csv(out_dir / "source-ecology.csv")

    fig, ax = plt.subplots(figsize=(14, 5.8))
    colors = plt.get_cmap("tab20")(np.linspace(0, 1, len(ordered_domains)))
    colors[-1] = (0.68, 0.70, 0.71, 1)
    left = np.zeros(len(providers))
    for index, domain in enumerate(ordered_domains):
        values = 100 * pivot[domain].to_numpy()
        ax.barh(range(len(providers)), values, left=left, label=domain, color=colors[index])
        for row, value in enumerate(values):
            if value >= 7:
                ax.text(left[row] + value / 2, row, f"{value:.0f}%", ha="center", va="center", fontsize=8)
        left += values
    ax.set_yticks(range(len(providers)), [DISPLAY_NAMES.get(item, item) for item in providers])
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xlabel("Mean share of returned slots across 80 primary query lists (%)")
    ax.set_title("Source ecology of the result lists")
    ax.grid(axis="x", alpha=0.25)
    ax.legend(fontsize=8, ncol=3, loc="lower left", bbox_to_anchor=(0, -0.34))
    fig.text(0.01, 0.01, "Host share is descriptive, not a relevance or authority score. Other hosts includes every domain outside the eight most common.", fontsize=8, color="#5c6870")
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    save_figure(fig, out_dir / "source-ecology.png")


def make_cost_per_slot(data: dict[str, pd.DataFrame], out_dir: Path) -> None:
    results = data["results"]
    runs = data["runs"]
    providers = provider_order(data)
    summary = data["summary"].set_index("provider")
    billing = data["billing"]
    slots = results.groupby("provider").size().reindex(providers).fillna(0)
    requests = runs.groupby("provider").size().reindex(providers).fillna(0)
    request_keys = runs[["query_id", "query_variant", "provider"]].drop_duplicates()
    result_counts = results.groupby(["query_id", "query_variant", "provider"]).size().rename("result_count").reset_index()
    request_counts = request_keys.merge(result_counts, on=["query_id", "query_variant", "provider"], how="left").fillna({"result_count": 0})
    complete_counts = request_counts.groupby("provider")["result_count"].apply(lambda values: int(values.eq(10).sum())).reindex(providers).fillna(0)
    complete = (100 * complete_counts / requests.replace(0, np.nan)).fillna(0)
    rows = []
    for provider in providers:
        match = billing[(billing["scope"] == "benchmark repetition 1") & billing["provider"].eq(provider)]
        if match.empty:
            cost = np.nan
            mode = "not supplied"
            basis = "No provider rate supplied"
        else:
            cost = float(match.iloc[0]["estimated_cost_usd"])
            mode = str(match.iloc[0].get("cost_mode", "estimate"))
            basis = str(match.iloc[0]["rate_basis"])
        returned_slots = float(slots.loc[provider])
        rows.append({
            "provider": provider,
            "requests": int(requests.loc[provider]),
            "returned_slots": int(returned_slots),
            "complete_10_count": int(complete_counts.loc[provider]),
            "complete_10_pct": float(complete.loc[provider]),
            "cost_usd": cost,
            "cost_mode": mode,
            "rate_basis": basis,
            "cost_per_1000_returned_slots": 1000 * cost / returned_slots if returned_slots else np.nan,
        })
    result = pd.DataFrame(rows)
    result.to_csv(out_dir / "cost-per-returned-slot.csv", index=False)
    fig, ax = plt.subplots(figsize=(10, 5.3))
    y = np.arange(len(result))
    bars = ax.barh(y, result["cost_per_1000_returned_slots"].fillna(0), color=[COLORS.get(item, "#7f8c8d") for item in result["provider"]])
    ax.set_yticks(y, [DISPLAY_NAMES.get(item, item) for item in result["provider"]])
    ax.invert_yaxis()
    ax.set_xlabel("Cost per 1,000 returned result slots (USD)")
    ax.set_title("Cost normalized by delivered result slots")
    ax.grid(axis="x", alpha=0.25)
    finite_costs = result["cost_per_1000_returned_slots"].dropna()
    offset = float(finite_costs.max()) * 0.02 if not finite_costs.empty else 0.02
    for bar, row in zip(bars, result.to_dict("records")):
        value = row["cost_per_1000_returned_slots"]
        completion = f"{row['complete_10_count']}/{row['requests']} complete"
        label = f"n/a | {completion}" if pd.isna(value) else f"${value:.3f} | {completion}"
        ax.text((bar.get_width() if np.isfinite(value) else 0) + offset, bar.get_y() + bar.get_height() / 2, label, va="center", fontsize=8)
    fig.tight_layout()
    save_figure(fig, out_dir / "cost-per-returned-slot.png")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    data = load_inputs(args.run_dir)
    make_latency_heatmap(data, args.out)
    make_completeness_heatmap(data, args.out)
    make_agreement_matrix(data, args.out)
    make_resolution_health(data, args.out)
    make_flagged_inventory(data, args.out)
    make_source_ecology(data, args.out)
    make_cost_per_slot(data, args.out)
    outputs = sorted(path for path in args.out.iterdir() if path.is_file())
    if len(outputs) < 12 or any(path.stat().st_size == 0 for path in outputs):
        raise SystemExit("Study-chart output validation failed")
    print(f"Wrote {len(outputs)} study-chart artifacts to {args.out}")


if __name__ == "__main__":
    main()
