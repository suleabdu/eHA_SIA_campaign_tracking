"""
06b_make_decision_brief.py -- one-page PDF decision brief for an Incident
Manager with 24 hours of mop-up capacity.

Design intent
-------------
Written for someone who will read this in under two minutes under time
pressure and then give instructions out loud. No GIS jargon in the
headline section; the statistical method is named once, in a small-print
footnote, not explained. Every number on this page traces to a specific
CSV produced by 04_coverage_reconciliation.py / 05_spatial_stats.py --
none are invented for the brief.

The brief deliberately does NOT lead with the Getis-Ord Gi* cluster
(Suwade, n=3) as the headline recommendation, even though it is the most
statistically rigorous result in the pipeline. That cluster is small (132
children) and its main value is that it is high-confidence, not that it is
high-impact. The larger, more decision-relevant pattern -- raw missed-
settlement counts and population heavily concentrated in a handful of
Idi-Oro wards -- is presented as the primary recommendation, with the
statistical cluster offered as a fast, low-risk first stop. This ordering
is a judgement call, explained in docs/METHODOLOGY.md sec.6.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_PDF = REPO_ROOT / "outputs" / "decision_brief.pdf"


FIG_H_IN = 11.69


def wrap_text(fig, x, y, text, fontsize, width_chars, line_spacing=1.35, **kwargs):
    """fig.text() with genuine, verifiable line wrapping. matplotlib's own
    wrap=True wraps against the canvas edge, not a target column width, and
    reliably overflows an A4 page at body-text font sizes -- confirmed by
    direct bbox measurement while building this script (a fontsize-9.6
    string wrapped to ~3850px on an 827px-wide page). Wrapping the string
    ourselves with textwrap before handing it to fig.text is the only
    reliable option in this environment. Returns the y (figure fraction)
    of the bottom of the rendered block, so callers can chain layout
    without hardcoded vertical offsets."""
    lines = textwrap.wrap(text, width=width_chars)
    wrapped = "\n".join(lines)
    fig.text(x, y, wrapped, fontsize=fontsize, va="top", linespacing=line_spacing, **kwargs)
    line_height_frac = (fontsize * line_spacing * 1.2) / 72.0 / FIG_H_IN
    return y - max(len(lines), 1) * line_height_frac


def main():
    sc = pd.read_csv(REPO_ROOT / "outputs" / "settlement_coverage.csv")
    wc = pd.read_csv(REPO_ROOT / "outputs" / "ward_coverage.csv")
    lga = pd.read_csv(REPO_ROOT / "outputs" / "lga_coverage.csv")
    clusters = pd.read_csv(REPO_ROOT / "outputs" / "missed_settlement_clusters.csv")

    accessible = sc[sc["security_classification"] == "Accessible"]
    n_miss = int((accessible["coverage_status"] == "Plausible miss").sum())
    pop_miss = accessible.loc[accessible["coverage_status"] == "Plausible miss", "target_population_under5"].sum()
    state_dose_cov = 100 * accessible["etally_doses_total"].sum() / accessible["target_population_under5"].sum()

    top_wards = wc.sort_values("n_plausible_miss", ascending=False).head(4)
    top_ward_names = top_wards["ward_name"].tolist()
    top_n_miss = int(top_wards["n_plausible_miss"].sum())
    top_pop = accessible[accessible["ward_name"].isin(top_ward_names) & (accessible["coverage_status"] == "Plausible miss")][
        "target_population_under5"
    ].sum()

    hot_actionable = clusters[
        (clusters["cluster_label"] == "Hot spot (missed-settlement cluster)")
        & (clusters["coverage_status"] == "Plausible miss")
    ]
    suwade_names = hot_actionable["settlement_name"].tolist()
    suwade_pop = hot_actionable["target_population_under5"].sum()

    blackout = wc[wc["complete_gps_blackout"] == True]  # noqa: E712

    n_excluded = int((sc["security_classification"] != "Accessible").sum())

    fig = plt.figure(figsize=(8.27, 11.69))  # A4 portrait
    fig.patch.set_facecolor("white")

    # ---- header ----
    fig.text(0.06, 0.965, "MOP-UP DEPLOYMENT BRIEF", fontsize=22, fontweight="bold", color="#7a0000")
    fig.text(0.06, 0.945, "Bansara State SIA -- for the Incident Manager -- 24-hour mop-up window", fontsize=11.5)
    fig.text(0.06, 0.930, "Campaign: 9-13 March 2026 house-to-house SIA, 4 LGAs. Brief prepared from GPS tracks + e-tally reconciliation.",
              fontsize=9, color="#444444")
    fig.add_artist(plt.Line2D([0.06, 0.94], [0.918, 0.918], color="#7a0000", linewidth=1.5, transform=fig.transFigure))

    # ---- headline numbers ----
    y0 = 0.885
    stats = [
        (f"{state_dose_cov:.0f}%", "State dose coverage\n(e-tally, accessible settlements)"),
        (f"{n_miss}", "Settlements with NO evidence\nof a visit (either source)"),
        (f"{pop_miss:,.0f}", "Under-5 children in those\nunconfirmed settlements"),
        (f"{n_excluded}", "Settlements excluded from\nthis campaign (security)"),
    ]
    box_w = 0.205
    for i, (num, label) in enumerate(stats):
        x = 0.06 + i * (box_w + 0.012)
        fig.add_artist(plt.Rectangle((x, y0 - 0.075), box_w, 0.075, transform=fig.transFigure,
                                       facecolor="#fbeee6", edgecolor="#d9834f", linewidth=1.0))
        fig.text(x + box_w / 2, y0 - 0.022, num, fontsize=19, fontweight="bold", ha="center", color="#7a2200")
        fig.text(x + box_w / 2, y0 - 0.062, label, fontsize=7.3, ha="center", va="top", color="#333333")

    # ---- Recommendation 1 ----
    ry = 0.775
    fig.text(0.06, ry, "RECOMMENDATION 1 -- Send most of your capacity here (highest impact)", fontsize=12.5, fontweight="bold")
    body1 = (
        f"Four wards in Idi-Oro LGA account for {top_n_miss} of the state's {n_miss} unconfirmed settlements "
        f"({100*top_n_miss/n_miss:.0f}%) and an estimated {top_pop:,.0f} under-5 children -- far more than any "
        f"other part of the state:"
    )
    y = wrap_text(fig, 0.06, ry - 0.026, body1, fontsize=9.4, width_chars=108) - 0.008
    table_y = y
    fig.text(0.08, table_y, "Ward", fontsize=9, fontweight="bold")
    fig.text(0.34, table_y, "Unconfirmed settlements", fontsize=9, fontweight="bold")
    fig.text(0.60, table_y, "Est. under-5 children", fontsize=9, fontweight="bold")
    for i, (_, r) in enumerate(top_wards.iterrows()):
        yy = table_y - 0.021 - i * 0.019
        pop_w = accessible[(accessible["ward_name"] == r["ward_name"]) & (accessible["coverage_status"] == "Plausible miss")][
            "target_population_under5"
        ].sum()
        fig.text(0.08, yy, f"{r['ward_name']} ({r['lga_name']})", fontsize=9)
        fig.text(0.40, yy, f"{int(r['n_plausible_miss'])}", fontsize=9)
        fig.text(0.63, yy, f"{pop_w:,.0f}", fontsize=9)
    caveat_y = table_y - 0.021 - len(top_wards) * 0.019 - 0.012
    caveat1 = (
        "Caveat: Idi-Oro's GPS data is the least reliable of the 4 LGAs (60% of surviving fixes flagged "
        "low-accuracy vs 3-12% elsewhere -- consistent with urban signal interference). Field teams should "
        "verify on arrival (ask a local informant / check for a house mark) rather than assume a skip."
    )
    y_after_rec1 = wrap_text(fig, 0.06, caveat_y, caveat1, fontsize=8.1, width_chars=122, color="#555555", style="italic")

    # ---- Recommendation 2 ----
    ry2 = y_after_rec1 - 0.026
    fig.text(0.06, ry2, "RECOMMENDATION 2 -- Fast, high-confidence first stop (if you want a quick win)", fontsize=12.5, fontweight="bold")
    body2 = (
        f"One small cluster of {len(suwade_names)} settlements in Suwade ward (Katsuma LGA) is the ONLY place in "
        f"the state where the GPS evidence and a formal statistical test agree independently: "
        f"{', '.join(suwade_names)} (~{suwade_pop:,.0f} children) -- the single most certain \"go here\" on the "
        f"map, though small."
    )
    y_after_rec2 = wrap_text(fig, 0.06, ry2 - 0.024, body2, fontsize=9.4, width_chars=108)

    # ---- Do NOT dispatch -- call instead ----
    ry3 = y_after_rec2 - 0.030
    fig.text(0.06, ry3, "DO NOT SEND A TEAM HERE YET -- CALL THE FIELD SUPERVISOR FIRST", fontsize=12.5, fontweight="bold", color="#7a0000")
    ward_list = ", ".join(f"{r['ward_name']} ({r['lga_name']})" for _, r in blackout.iterrows())
    body3 = (
        f"{len(blackout)} ward(s) -- {ward_list} -- have ZERO surviving GPS points for the whole campaign, "
        f"despite the paper tally showing ~80%+ coverage there. We cannot tell, from data alone, whether these "
        f"settlements were visited or the logger failed. A supervisor phone/radio check-in, or retrieving the "
        f"logger for a manual download, answers this far more cheaply than a field visit."
    )
    y_after_rec3 = wrap_text(fig, 0.06, ry3 - 0.024, body3, fontsize=9.4, width_chars=108)

    # ---- honesty section ----
    ry4 = y_after_rec3 - 0.022
    box_top = ry4
    fig.text(0.075, ry4 - 0.018, "WHAT THIS BRIEF DOES NOT TELL YOU", fontsize=11, fontweight="bold")
    bullets = [
        "\"Unconfirmed\" is not \"unvaccinated\". A settlement can show no GPS or tally record because a team "
        "visited but found no eligible children, faced a refusal, ran out of stock, or the record wasn't logged.",
        f"The Suwade cluster does not mean settlements outside it are safe. Of {n_miss} unconfirmed settlements "
        f"state-wide, only {len(suwade_names)} sit inside a statistically confirmed cluster -- the rest are "
        "scattered and just as real a concern, only harder to target efficiently in 24 hours.",
        "A settlement near a statistical cluster is not automatically unconfirmed itself: of 20 settlements in "
        "the Suwade neighbourhood, 17 are already confirmed visited by GPS or tally -- only 3 are not.",
        f"{n_excluded} settlements are excluded from every number here as formally inaccessible / partially "
        "accessible for security -- a deliberate planning decision upstream of this brief, not a coverage gap.",
        "This is a targeting tool for scarce field hours, showing where the evidence for gaps concentrates -- "
        "not a certified record of which individual children have or have not been vaccinated.",
    ]
    yy = ry4 - 0.044
    for b in bullets:
        fig.text(0.08, yy, "-", fontsize=9, fontweight="bold", va="top")
        yy = wrap_text(fig, 0.10, yy, b, fontsize=8.1, width_chars=112, line_spacing=1.18) - 0.007
    box_bottom = yy + 0.004
    fig.add_artist(plt.Rectangle((0.055, box_bottom), 0.89, box_top - box_bottom, transform=fig.transFigure,
                                   facecolor="#f5f5f5", edgecolor="#999999", linewidth=0.8, zorder=0))

    # ---- footer ----
    footer = (
        "Prepared from: cleaned_tracks (97,864 of 956,702 raw GPS fixes retained after QA), e-tally reconciliation, "
        "and local Getis-Ord Gi* cluster analysis (k=8 NN weights, 999-permutation p-values, Benjamini-Hochberg FDR "
        "q=0.05). Full methodology: docs/METHODOLOGY.md. Companion technical map: A3_missed_settlement_clusters.pdf."
    )
    wrap_text(fig, 0.06, 0.048, footer, fontsize=7, width_chars=128, color="#666666")

    fig.savefig(OUT_PDF, dpi=300)
    plt.close(fig)
    print(f"[brief] wrote {OUT_PDF}")


if __name__ == "__main__":
    main()
