"""
05_spatial_stats.py -- statistically significant clusters of missed
settlements.

Statistic: Getis-Ord Gi* (local)
-----------------------------------------------------------------------------
Gi* was chosen over Local Moran's I because the operational question is
"where is missed-ness concentrated" (a hot-spot detection question about
the magnitude of a single non-negative indicator), not "where are similar
values next to dissimilar values" (Local Moran's I's strength, better suited
to spatial outlier detection). Gi* also has a direct, decision-relevant
interpretation for a mop-up brief: a significant positive Gi* z-score means
a settlement AND ITS NEIGHBOURS together have more missed settlements than
would be expected if misses were scattered at random across the study area
-- i.e. a genuine geographic concentration worth sending a team to, rather
than an isolated settlement that just happens to be missed on its own.

Analysis universe
-----------------------------------------------------------------------------
Only settlements with a *determinate* coverage_status are included in the
point pattern: "Visited (track-confirmed)", "Tally-only (no GPS
confirmation)" and "Plausible miss". "Planned exclusion (security)" and
"Indeterminate - no data available" settlements are dropped from the
pattern entirely (not coded as 0/not-missed), because coding a
data-blackout or a deliberately-excluded settlement as "not missed" would
bias hot-spot detection toward ward boundaries and security-excluded zones
for reasons that have nothing to do with programme performance.

The binary variable tested is:
    is_missed = 1 if coverage_status == "Plausible miss" else 0
(i.e. "Tally-only" settlements are coded 0 -- they have positive evidence of
a visit, just not GPS-confirmed evidence; only settlements with NO evidence
from either source, in a ward we know had active track or e-tally coverage,
count as "missed" for this statistic.)

Spatial weights
-----------------------------------------------------------------------------
k-nearest-neighbour (k=8) weights on settlement point locations, row-
standardised. KNN rather than a fixed distance band or contiguity, because
settlement density varies enormously across the study area (LGA-level
nearest-neighbour distances range from a median of ~670 m in Idi-Oro to
~1,090 m in Ilela -- see docs/METHODOLOGY.md sec.1). A fixed distance band
would give sparsely-settled wards almost no neighbours and densely-settled
ones far too many; KNN normalises neighbourhood size across that density
gradient. k=8 is a conventional choice for local cluster detection that
balances local sensitivity against a stable enough neighbourhood for the
permutation test.

Significance testing
-----------------------------------------------------------------------------
Conditional permutation (999 permutations, esda's default) to get an
empirical pseudo p-value per settlement, NOT a reference to the normal
distribution -- the binary, spatially clustered nature of this variable
means the normal approximation is unreliable at the tails. Because ~2,467
settlements are tested simultaneously, a naive p < 0.05 cut-off would be
expected to flag roughly 100+ settlements as "significant" by chance alone.
A Benjamini-Hochberg false discovery rate correction (q = 0.05) is applied
across all tested settlements before anything is labelled a hot spot.

What this analysis does NOT license (stated again in the decision brief)
-----------------------------------------------------------------------------
A significant Gi* hot spot is evidence about a NEIGHBOURHOOD, not about any
individual settlement inside it, and says nothing about any individual
child. It does not mean every settlement in the hot spot was actually
missed (some will be false negatives of the track method); it does not mean
settlements outside a hot spot are safe (isolated misses can and do exist
outside any detected cluster); and it is a statement about where the
evidence for non-coverage clusters spatially, not a certified list of
unvaccinated children.
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from libpysal.weights import KNN
from esda.getisord import G_Local

sys.path.insert(0, str(Path(__file__).resolve().parent))

REPO_ROOT = Path(__file__).resolve().parents[1]
GPKG = REPO_ROOT / "outputs" / "bansara_sia.gpkg"
PROJ_CRS = "EPSG:32632"

K_NEIGHBORS = 8
N_PERMUTATIONS = 999
FDR_Q = 0.05
RANDOM_SEED = 42

DETERMINATE_STATUSES = [
    "Visited (track-confirmed)",
    "Tally-only (no GPS confirmation)",
    "Plausible miss",
]


def benjamini_hochberg(pvals: np.ndarray, q: float) -> np.ndarray:
    """Returns a boolean array: True where the null is rejected under BH FDR control."""
    n = len(pvals)
    order = np.argsort(pvals)
    ranked = pvals[order]
    thresh = (np.arange(1, n + 1) / n) * q
    below = ranked <= thresh
    if not below.any():
        return np.zeros(n, dtype=bool)
    max_rank = np.max(np.where(below)[0])
    reject_sorted = np.zeros(n, dtype=bool)
    reject_sorted[: max_rank + 1] = True
    reject = np.zeros(n, dtype=bool)
    reject[order] = reject_sorted
    return reject


def main():
    print("[stats] loading settlement_coverage ...")
    cov = gpd.read_file(GPKG, layer="settlement_coverage").to_crs(PROJ_CRS)

    analysis = cov[cov["coverage_status"].isin(DETERMINATE_STATUSES)].copy().reset_index(drop=True)
    print(f"[stats] analysis universe: {len(analysis)} of {len(cov)} settlements "
          f"(excluded {len(cov) - len(analysis)} security-excluded / indeterminate settlements)")

    analysis["is_missed"] = (analysis["coverage_status"] == "Plausible miss").astype(int)
    print(f"[stats] {analysis['is_missed'].sum()} of {len(analysis)} settlements in the analysis "
          f"universe are 'Plausible miss' ({100*analysis['is_missed'].mean():.1f}%)")

    coords = np.c_[analysis.geometry.x, analysis.geometry.y]
    w = KNN.from_array(coords, k=K_NEIGHBORS)
    w.transform = "R"
    print(f"[stats] spatial weights: k-nearest-neighbours, k={K_NEIGHBORS}, row-standardised")

    np.random.seed(RANDOM_SEED)
    gi = G_Local(analysis["is_missed"].values, w, transform="B", star=True, permutations=N_PERMUTATIONS, seed=RANDOM_SEED)

    analysis["gi_star_z"] = gi.Zs
    analysis["gi_star_p_sim"] = gi.p_sim
    reject = benjamini_hochberg(analysis["gi_star_p_sim"].values, FDR_Q)
    analysis["fdr_significant"] = reject

    def label(row):
        if not row["fdr_significant"]:
            return "Not significant"
        return "Hot spot (missed-settlement cluster)" if row["gi_star_z"] > 0 else "Cold spot (well-covered cluster)"

    analysis["cluster_label"] = analysis.apply(label, axis=1)

    print("\n[stats] cluster_label counts (after Benjamini-Hochberg FDR, q=0.05):")
    print(analysis["cluster_label"].value_counts().to_string())

    n_raw_sig = int((analysis["gi_star_p_sim"] < 0.05).sum())
    n_fdr_sig = int(reject.sum())
    print(f"\n[stats] {n_raw_sig} settlements have an uncorrected pseudo p-value < 0.05; "
          f"only {n_fdr_sig} survive Benjamini-Hochberg FDR correction at q={FDR_Q}. "
          f"This gap is the multiple-testing effect the methodology note above warns about.")

    hot = analysis[analysis["cluster_label"] == "Hot spot (missed-settlement cluster)"]
    print(f"\n[stats] {len(hot)} settlements sit inside a statistically significant "
          f"missed-settlement hot spot")
    if len(hot):
        print(hot.groupby(["ward_name", "lga_name"]).size().sort_values(ascending=False).to_string())

    # ---- write outputs ----
    out = analysis.to_crs("EPSG:4326")
    for c in ["first_visit_ts", "last_visit_ts"]:
        if c in out.columns:
            out[c] = out[c].astype(str)
    out.to_file(GPKG, layer="missed_settlement_clusters", driver="GPKG", mode="w")
    print(f"\n[stats] wrote missed_settlement_clusters layer: {len(out)} rows")

    out.drop(columns="geometry").to_csv(REPO_ROOT / "outputs" / "missed_settlement_clusters.csv", index=False)

    ward_hot = (
        hot.groupby(["ward_code", "ward_name", "lga_name"]).size().reset_index(name="n_hotspot_settlements")
        if len(hot) else pd.DataFrame(columns=["ward_code", "ward_name", "lga_name", "n_hotspot_settlements"])
    )
    ward_hot.to_csv(REPO_ROOT / "outputs" / "ward_hotspot_summary.csv", index=False)
    print(f"[stats] wrote outputs/ward_hotspot_summary.csv: {len(ward_hot)} wards contain hot-spot settlements")


if __name__ == "__main__":
    main()
