"""
06a_make_a3_map.py -- A3 PDF map of missed settlement clusters for a
technical (GIS/M&E) audience.

Design notes
------------
No external basemap or locator inset is fetched -- the pipeline is meant to
be reproducible fully offline from the data pack alone (see README /
docs/METHODOLOGY.md "why GeoPackage" note), so the only spatial context
available is what's in boundaries.gpkg. Instead of a Nigeria-wide locator
inset (which would need an external admin-0 boundary), the inset panel
zooms into the dominant hot-spot cluster (Suwade ward) for operational
detail, which is more directly useful to the audience this map is for.
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D
from matplotlib.backends.backend_pdf import PdfPages
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

REPO_ROOT = Path(__file__).resolve().parents[1]
GPKG = REPO_ROOT / "outputs" / "bansara_sia.gpkg"
PROJ_CRS = "EPSG:32632"
OUT_PDF = REPO_ROOT / "outputs" / "A3_missed_settlement_clusters.pdf"

STATUS_COLORS = {
    "Visited (track-confirmed)": "#9e9e9e",
    "Tally-only (no GPS confirmation)": "#7fb3e8",
    "Plausible miss": "#f2a154",
    "Indeterminate - no data available": "#c9c9c9",
    "Planned exclusion (security)": "#5a5a5a",
}


def draw_north_arrow(ax, x, y, size):
    ax.annotate(
        "N", xy=(x, y + size), xytext=(x, y - size),
        ha="center", va="center", fontsize=13, fontweight="bold",
        arrowprops=dict(arrowstyle="-|>", linewidth=2.2, color="black"),
        xycoords=ax.transAxes, textcoords=ax.transAxes,
    )


def draw_scalebar(ax, gdf_extent_m, x0_frac=0.04, y0_frac=0.045):
    """Simple projected-unit scale bar (map is in EPSG:32632, meters)."""
    xmin, xmax = ax.get_xlim()
    span_m = xmax - xmin
    # pick a round number close to 20% of the map width
    candidates = [500, 1000, 2000, 5000, 10000, 20000]
    bar_m = min(candidates, key=lambda c: abs(c - 0.2 * span_m))
    x0 = xmin + x0_frac * span_m
    ymin, ymax = ax.get_ylim()
    y0 = ymin + y0_frac * (ymax - ymin)
    ax.plot([x0, x0 + bar_m], [y0, y0], color="black", linewidth=3, solid_capstyle="butt")
    ax.plot([x0, x0], [y0, y0 + 0.01 * (ymax - ymin)], color="black", linewidth=1.5)
    ax.plot([x0 + bar_m, x0 + bar_m], [y0, y0 + 0.01 * (ymax - ymin)], color="black", linewidth=1.5)
    label = f"{bar_m/1000:.0f} km" if bar_m >= 1000 else f"{bar_m:.0f} m"
    ax.text(x0 + bar_m / 2, y0 - 0.022 * (ymax - ymin), label, ha="center", va="top", fontsize=9)


def main():
    cov = gpd.read_file(GPKG, layer="settlement_coverage").to_crs(PROJ_CRS)
    clusters = gpd.read_file(GPKG, layer="missed_settlement_clusters").to_crs(PROJ_CRS)
    wards = gpd.read_file(REPO_ROOT / "data" / "raw" / "boundaries.gpkg", layer="wards").to_crs(PROJ_CRS)
    lgas = gpd.read_file(REPO_ROOT / "data" / "raw" / "boundaries.gpkg", layer="lgas").to_crs(PROJ_CRS)
    ward_cov = pd.read_csv(REPO_ROOT / "outputs" / "ward_coverage.csv")

    # IMPORTANT interpretive note (see docs/METHODOLOGY.md sec.5): a
    # significant local Gi* z-score describes a NEIGHBOURHOOD, not the
    # individual settlement it's attached to. Of the 20 settlements with a
    # significant hot-spot label, 9 are already "Visited (track-confirmed)"
    # and 8 are "Tally-only" -- only 3 are themselves actually classified
    # "Plausible miss". Those 3 (all in Suwade ward) are the only settlements
    # for which BOTH the individual evidence AND the neighbourhood-level
    # statistic agree, and are shown as the high-confidence actionable
    # cluster. The other 17 are shown only as thin outlines marking the
    # statistical neighbourhood, not as dispatch targets.
    hot_all = clusters[clusters["cluster_label"] == "Hot spot (missed-settlement cluster)"]
    hot_actionable = hot_all[hot_all["coverage_status"] == "Plausible miss"]
    hot_context_only = hot_all[hot_all["coverage_status"] != "Plausible miss"]
    blackout_wards = ["W035", "W039"]  # Dazata, Nungoni-Arewa -- see 04_coverage_reconciliation.py

    fig = plt.figure(figsize=(16.54, 11.69))  # A3 landscape, inches
    gs = fig.add_gridspec(1, 1, left=0.045, right=0.72, top=0.94, bottom=0.06)
    ax = fig.add_subplot(gs[0, 0])

    # ---- PRIMARY SIGNAL: choropleth of raw "Plausible miss" settlement
    # counts per ward. This -- not the statistical cluster test -- is the
    # dominant, decision-relevant pattern in this dataset: raw miss counts
    # are heavily concentrated in Idi-Oro's wards (up to 26 settlements /
    # ward) even where individual settlements don't clear the FDR-corrected
    # local significance bar (see docs/METHODOLOGY.md sec.5 for why high
    # settlement density can keep absolute counts from being LOCALLY
    # concentrated enough to register on Gi* at k=8).
    ward_plot = wards.merge(ward_cov[["ward_code", "n_plausible_miss"]], on="ward_code", how="left")
    ward_plot["n_plausible_miss"] = ward_plot["n_plausible_miss"].fillna(0)
    ward_plot.plot(
        ax=ax, column="n_plausible_miss", cmap="OrRd", edgecolor="none", alpha=0.55, zorder=1,
        vmin=0, vmax=ward_plot["n_plausible_miss"].max(),
    )

    wards.boundary.plot(ax=ax, color="#888888", linewidth=0.5, zorder=2)
    lgas.boundary.plot(ax=ax, color="black", linewidth=1.6, zorder=3)

    blackout_geom = wards[wards["ward_code"].isin(blackout_wards)]
    blackout_geom.plot(ax=ax, facecolor="none", edgecolor="#4472c4", linewidth=2.2, hatch="//", zorder=3)

    for status, color in STATUS_COLORS.items():
        sub = cov[cov["coverage_status"] == status]
        if len(sub) == 0:
            continue
        marker = {"Planned exclusion (security)": "^", "Indeterminate - no data available": "x"}.get(status, "o")
        ax.scatter(
            sub.geometry.x, sub.geometry.y, s=9 if status == "Plausible miss" else 5,
            c=color, marker=marker, linewidths=0.3, edgecolors="none" if marker != "x" else color,
            zorder=4, alpha=0.85, label=status,
        )

    # statistical-neighbourhood context (thin outline only, NOT a dispatch signal)
    ax.scatter(
        hot_context_only.geometry.x, hot_context_only.geometry.y, s=55, facecolor="none",
        edgecolor="#d62728", linewidths=1.1, marker="o", zorder=5,
        label="In significant cluster zone\n(already accounted for)",
    )
    # high-confidence actionable cluster: significant AND itself unconfirmed
    ax.scatter(
        hot_actionable.geometry.x, hot_actionable.geometry.y, s=110, facecolor="#d62728",
        edgecolor="black", linewidths=1.2, marker="o", zorder=6,
        label="High-confidence cluster\n(significant AND unconfirmed, n=3)",
    )

    for _, l in lgas.iterrows():
        cx, cy = l.geometry.representative_point().x, l.geometry.representative_point().y
        ax.annotate(l["lga_name"], xy=(cx, cy), fontsize=12, fontweight="bold", ha="center",
                    color="black", alpha=0.55, zorder=3)

    # label the top-6 wards by raw miss count directly on the map
    top6 = ward_cov.sort_values("n_plausible_miss", ascending=False).head(6)
    for _, r in top6.iterrows():
        w = wards[wards["ward_code"] == r["ward_code"]]
        if len(w) == 0:
            continue
        cx, cy = w.geometry.iloc[0].centroid.x, w.geometry.iloc[0].centroid.y
        ax.annotate(
            f"{r['ward_name']}\n{int(r['n_plausible_miss'])} missed", xy=(cx, cy), fontsize=7.5, fontweight="bold",
            ha="center", color="#7a2200", zorder=7,
            bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="#b34700", alpha=0.85, linewidth=0.7),
        )

    ax.set_xlim(wards.total_bounds[0] - 1500, wards.total_bounds[2] + 1500)
    ax.set_ylim(wards.total_bounds[1] - 1500, wards.total_bounds[3] + 1500)
    ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(True); spine.set_color("black"); spine.set_linewidth(1.2)

    draw_north_arrow(ax, 0.965, 0.90, 0.035)
    draw_scalebar(ax, wards.total_bounds)

    # ---- title block ----
    fig.text(0.045, 0.975, "Bansara State SIA -- Missed Settlement Clusters", fontsize=20, fontweight="bold")
    fig.text(0.045, 0.955, "Raw miss density by ward (shading) and statistically significant clusters (Gi*), 9-13 March 2026 house-to-house SIA",
              fontsize=11, color="#333333")

    # ---- legend panel (right side) ----
    lax = fig.add_axes([0.735, 0.46, 0.24, 0.48])
    lax.axis("off")
    handles = [
        Line2D([0], [0], marker="o", color="none", markerfacecolor=c, markeredgecolor="none", markersize=8, label=s)
        for s, c in STATUS_COLORS.items() if s not in ("Planned exclusion (security)", "Indeterminate - no data available")
    ]
    handles.append(Line2D([0], [0], marker="^", color="none", markerfacecolor=STATUS_COLORS["Planned exclusion (security)"],
                            markeredgecolor="none", markersize=8, label="Planned exclusion (security)"))
    handles.append(Line2D([0], [0], marker="x", color=STATUS_COLORS["Indeterminate - no data available"],
                            markersize=8, label="Indeterminate - no data available", linestyle="none"))
    handles.append(Line2D([0], [0], marker="o", color="none", markerfacecolor="none", markeredgecolor="#d62728",
                            markersize=9, label="In significant cluster zone\n(neighbourhood signal only)"))
    handles.append(Line2D([0], [0], marker="o", color="none", markerfacecolor="#d62728", markeredgecolor="black",
                            markersize=11, label="High-confidence cluster\n(significant AND unconfirmed)"))
    handles.append(mpatches.Patch(facecolor="none", edgecolor="#4472c4", hatch="//", linewidth=1.5,
                                    label="Ward: complete GPS\nblackout (0 track points)"))
    handles.append(mpatches.Patch(facecolor="#e37b52", edgecolor="none", alpha=0.55,
                                    label="Shading: raw 'Plausible\nmiss' count per ward"))
    lax.legend(handles=handles, loc="upper left", frameon=False, fontsize=8, labelspacing=1.35, handletextpad=0.8)
    lax.text(0, 1.0, "LEGEND", fontsize=10, fontweight="bold", transform=lax.transAxes, va="bottom")

    # ---- interpretive note panel ----
    note_ax = fig.add_axes([0.735, 0.24, 0.24, 0.20])
    note_ax.axis("off")
    note_text = (
        "READ THIS BEFORE ACTING\n\n"
        "Of the 20 settlements in a statistically significant Gi* cluster, only 3 (Suwade ward) are themselves "
        "still unconfirmed -- the other 17 sit inside the same statistical neighbourhood but are already "
        "track- or tally-confirmed. Cluster significance describes an AREA, not a certified list of individual "
        "missed settlements.\n\n"
        "The larger, more decision-relevant pattern is the raw miss count by ward (shading): Idi-Oro's wards "
        "carry by far the most unconfirmed settlements and children, even though their individual settlements "
        "less often clear the local significance bar -- high settlement density there spreads misses across "
        "more neighbours. See the decision brief for the recommended use of this."
    )
    note_ax.text(0, 1, note_text, fontsize=7.6, va="top", ha="left", wrap=True,
                  bbox=dict(boxstyle="round,pad=0.5", fc="#fff6e8", ec="#b34700", linewidth=0.8))

    # ---- inset: zoom on dominant statistical cluster (Suwade ward) ----
    iax = fig.add_axes([0.735, 0.03, 0.24, 0.18])
    suwade = wards[wards["ward_name"] == "Suwade"]
    if len(suwade):
        wgeom = suwade.geometry.iloc[0]
        buf = wgeom.buffer(400)
        minx, miny, maxx, maxy = buf.bounds
        wards.boundary.plot(ax=iax, color="#888888", linewidth=0.6)
        suwade.boundary.plot(ax=iax, color="black", linewidth=1.4)
        cov_local = cov.cx[minx:maxx, miny:maxy]
        for status, color in STATUS_COLORS.items():
            sub = cov_local[cov_local["coverage_status"] == status]
            iax.scatter(sub.geometry.x, sub.geometry.y, s=16, c=color, zorder=4)
        hot_local = hot_actionable.cx[minx:maxx, miny:maxy]
        iax.scatter(hot_local.geometry.x, hot_local.geometry.y, s=100, facecolor="#d62728",
                    edgecolor="black", linewidths=1.0, zorder=6)
        iax.set_xlim(minx, maxx); iax.set_ylim(miny, maxy)
        iax.set_aspect("equal")
        iax.set_xticks([]); iax.set_yticks([])
        for spine in iax.spines.values():
            spine.set_visible(True); spine.set_color("black"); spine.set_linewidth(1.0)
        iax.set_title("Inset: Suwade ward (Katsuma LGA)\nhigh-confidence cluster, n=3 unconfirmed", fontsize=8.5, fontweight="bold")

    # ---- footer: data sources, method, projection ----
    footer = (
        "Data sources: raw GPS team tracks (n=956,702 fixes), settlement masterlist, ward/LGA boundaries, "
        "daily e-tally, inaccessible-settlement list -- Bansara State SIA data pack, 9-13 March 2026.\n"
        "Method: settlements attributed to cleaned tracks via accuracy-aware adaptive buffered proximity (base radius 50-150m "
        "+ min(reported accuracy, 60m)); a settlement is 'Plausible miss' only where NEITHER GPS nor e-tally shows evidence "
        "of a visit AND its ward had independently confirmed campaign activity that week. Hot spots: local Getis-Ord Gi*, "
        "k=8 nearest-neighbour row-standardised weights, 999-permutation pseudo p-values, Benjamini-Hochberg FDR q=0.05.\n"
        "A significant Gi* result describes a NEIGHBOURHOOD, not a certified list of individually missed settlements or "
        "unvaccinated children -- of 20 settlements in a significant cluster, only 3 are themselves still unconfirmed. "
        "See docs/METHODOLOGY.md sec.5 for the full interpretive caveat and the decision brief for recommended action.\n"
        "Coordinate reference system: WGS84 / UTM zone 32N (EPSG:32632). Prepared for technical / GIS audience review. "
        "Reproducible pipeline: github.com/<org>/bansara-sia-tracking, commit-tagged output."
    )
    fig.text(0.045, 0.035, footer, fontsize=6.6, color="#333333", va="bottom", wrap=True, linespacing=1.5)

    with PdfPages(OUT_PDF) as pdf:
        pdf.savefig(fig, dpi=300)
    plt.close(fig)
    print(f"[map] wrote {OUT_PDF}")


if __name__ == "__main__":
    main()
