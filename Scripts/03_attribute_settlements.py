"""
03_attribute_settlements.py -- attribute cleaned GPS tracks to planned
settlements and determine which settlements were visited.

Method: accuracy-aware adaptive buffered proximity
----------------------------------------------------
Chosen over a fixed flat-radius buffer or a settlement micro-grid for two
reasons documented in full in docs/METHODOLOGY.md sec.3:

1.  Settlement spacing varies a great deal (5th percentile nearest-neighbour
    distance ~194 m; median ~734 m; see METHODOLOGY). A single flat radius
    is either too tight for sparsely spaced rural settlements or too loose
    for closely spaced ones, and a fixed radius large enough to tolerate
    degraded urban GPS risks conflating adjacent settlements everywhere
    else. The buffer radius is therefore set per settlement as a function
    of its own local spacing:
        base_radius = clip(0.4 x nearest-neighbour distance, 50 m, 150 m)

2.  Positional accuracy is not uniform across the study area. Idi-Oro
    (urban) shows median reported accuracy of 34.6 m against 8-9 m
    elsewhere, and 60% of its cleaned fixes are QA-flagged as degraded
    (see 02_qc_tracks.py QA-6 and docs/METHODOLOGY.md sec.3.1) -- a
    classic urban multipath signature. Rather than hand a blanket wider
    buffer to an entire LGA (which would also raise the risk of
    misattributing a visit to the wrong, closely-spaced settlement), each
    point's OWN reported accuracy_m is added to the settlement's base
    radius at attribution time, capped at 60 m so that a single very poor
    fix cannot dominate:
        effective_tolerance(point, settlement) = base_radius(settlement)
                                                    + min(point.accuracy_m, 60)

    This makes the tolerance self-adjusting: degraded urban fixes get more
    benefit of the doubt exactly where degradation is concentrated, without
    changing anything in areas where GPS quality is already good.

A settlement micro-grid (tessellating each settlement's footprint into
sub-cells and testing point-in-cell) was considered as an alternative. It
was not implemented as the primary method because the settlement masterlist
gives point centroids, not settlement footprints/extents -- a micro-grid
would need an assumed footprint radius per settlement type (Village /
Hamlet / Settlement / Urban block), which introduces another unvalidated
parameter without removing the accuracy problem it was meant to solve. The
buffer-radius sensitivity sweep below (50 / 100 / 150 / 250 m fixed radii)
is run instead as the required tolerance-justification / robustness check
and stands in for a full micro-grid implementation; docs/METHODOLOGY.md
describes how a micro-grid would extend this if settlement footprint
polygons became available.

Ambiguous attribution
----------------------
Where a point falls within tolerance of more than one settlement's buffer,
it is attributed to the nearest one and the affected settlement pair is
flagged `ambiguous_neighbor = True` for manual review -- these are exactly
the closely-spaced settlements a flat radius would have gotten wrong.

Outputs
-------
    settlement_visits        -- one row per planned settlement, with
                                visit evidence, buffer radius used, and
                                ambiguity flag
    track_settlement_matches -- one row per (track point, settlement) match,
                                for audit / QGIS drill-down
    buffer_sensitivity.csv    -- visited/not-visited counts at 4 fixed radii
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent))

REPO_ROOT = Path(__file__).resolve().parents[1]
GPKG = REPO_ROOT / "outputs" / "bansara_sia.gpkg"
PROJ_CRS = "EPSG:32632"  # WGS84 / UTM zone 32N -- see docs/METHODOLOGY.md sec.1
# for why this CRS: the whole study area (lon 7.00-8.44 E) sits inside a
# single UTM zone, giving sub-1:2500 scale distortion, and meters are
# required for every buffer/distance/area operation in this pipeline.

BASE_RADIUS_MIN_M = 50.0
BASE_RADIUS_MAX_M = 150.0
BASE_RADIUS_FRACTION_OF_NN = 0.4
ACCURACY_BONUS_CAP_M = 60.0
SENSITIVITY_RADII_M = [50, 100, 150, 250]


def load_settlements() -> gpd.GeoDataFrame:
    sm = pd.read_csv(REPO_ROOT / "data" / "raw" / "settlement_masterlist.csv", dtype={"settlement_id": str})

    # DATA QUALITY NOTE (docs/METHODOLOGY.md sec.0): 38 of the 40 ward_name
    # values in the settlement masterlist are spelled inconsistently across
    # rows for the same ward_code (case and whitespace variants, e.g.
    # "Ekngoyi" / "Ekngoyi " / "EKNGOYI"). Grouping by the masterlist's own
    # ward_name silently fragments every ward-level aggregate downstream.
    # ward_code is clean and is treated as the sole authoritative join key;
    # ward_name and lga_name are overwritten here from boundaries.gpkg's
    # `wards` layer, which has exactly one canonical spelling per ward_code.
    wards = gpd.read_file(REPO_ROOT / "data" / "raw" / "boundaries.gpkg", layer="wards")
    canonical = wards[["ward_code", "ward_name", "lga_code", "lga_name"]].drop_duplicates("ward_code")
    sm = sm.drop(columns=["ward_name", "lga_name"]).merge(canonical, on=["ward_code", "lga_code"], how="left")

    gdf = gpd.GeoDataFrame(
        sm, geometry=gpd.points_from_xy(sm["longitude"], sm["latitude"]), crs="EPSG:4326"
    ).to_crs(PROJ_CRS)
    return gdf


def load_inaccessible() -> pd.DataFrame:
    return pd.read_csv(
        REPO_ROOT / "data" / "raw" / "inaccessible_settlements.csv", dtype={"settlement_id": str}
    )


def compute_base_radius(settlements: gpd.GeoDataFrame) -> np.ndarray:
    coords = np.c_[settlements.geometry.x, settlements.geometry.y]
    tree = cKDTree(coords)
    dist, _ = tree.query(coords, k=2)
    nn_dist = dist[:, 1]
    radius = np.clip(nn_dist * BASE_RADIUS_FRACTION_OF_NN, BASE_RADIUS_MIN_M, BASE_RADIUS_MAX_M)
    return radius, nn_dist


def attribute(tracks: gpd.GeoDataFrame, settlements: gpd.GeoDataFrame, base_radius: np.ndarray) -> pd.DataFrame:
    """Accuracy-aware adaptive buffered proximity match. Returns a long table
    of (row_uid, settlement_id, distance_m, tolerance_m) for every match
    within tolerance, keeping ALL matches (not just nearest) so ambiguity
    can be detected downstream."""
    s_coords = np.c_[settlements.geometry.x, settlements.geometry.y]
    t_coords = np.c_[tracks.geometry.x, tracks.geometry.y]
    s_tree = cKDTree(s_coords)

    max_possible_tol = base_radius.max() + ACCURACY_BONUS_CAP_M
    # candidate settlements within the loosest possible tolerance of each point
    candidate_lists = s_tree.query_ball_point(t_coords, r=max_possible_tol)

    acc = tracks["accuracy_m"].fillna(ACCURACY_BONUS_CAP_M).clip(upper=ACCURACY_BONUS_CAP_M).values
    row_uids = tracks["row_uid"].values
    team_ids = tracks["team_id"].values
    timestamps = tracks["timestamp"].values

    matches = []
    for i, cand_idx in enumerate(candidate_lists):
        if not cand_idx:
            continue
        px, py = t_coords[i]
        for j in cand_idx:
            sx, sy = s_coords[j]
            d = np.hypot(px - sx, py - sy)
            tol = base_radius[j] + acc[i]
            if d <= tol:
                matches.append((row_uids[i], team_ids[i], timestamps[i], j, d, tol))

    out = pd.DataFrame(
        matches, columns=["row_uid", "team_id", "timestamp", "settlement_idx", "distance_m", "tolerance_m"]
    )
    out["settlement_id"] = settlements["settlement_id"].values[out["settlement_idx"].values]
    return out


def build_settlement_visits(
    settlements: gpd.GeoDataFrame,
    base_radius: np.ndarray,
    nn_dist: np.ndarray,
    matches: pd.DataFrame,
    inaccessible: pd.DataFrame,
) -> gpd.GeoDataFrame:
    settlements = settlements.copy()
    settlements["base_radius_m"] = base_radius
    settlements["nn_dist_m"] = nn_dist

    # nearest-settlement-only view for clean per-point attribution / ambiguity flag
    nearest = matches.sort_values("distance_m").drop_duplicates(subset=["row_uid"], keep="first")
    n_matches_per_point = matches.groupby("row_uid")["settlement_id"].nunique()
    ambiguous_points = set(n_matches_per_point[n_matches_per_point > 1].index)
    nearest["ambiguous"] = nearest["row_uid"].isin(ambiguous_points)

    agg = nearest.groupby("settlement_id").agg(
        n_track_points=("row_uid", "size"),
        n_visiting_teams=("team_id", "nunique"),
        visiting_teams=("team_id", lambda s: ",".join(sorted(set(s)))),
        first_visit_ts=("timestamp", "min"),
        last_visit_ts=("timestamp", "max"),
        any_ambiguous_match=("ambiguous", "any"),
    )

    settlements = settlements.merge(agg, on="settlement_id", how="left")
    settlements["n_track_points"] = settlements["n_track_points"].fillna(0).astype(int)
    settlements["n_visiting_teams"] = settlements["n_visiting_teams"].fillna(0).astype(int)
    settlements["visiting_teams"] = settlements["visiting_teams"].fillna("")
    settlements["any_ambiguous_match"] = settlements["any_ambiguous_match"].fillna(False)
    settlements["track_visited"] = settlements["n_track_points"] > 0

    ia = inaccessible.set_index("settlement_id")["security_classification"]
    settlements["security_classification"] = settlements["settlement_id"].map(ia).fillna("Accessible")

    # flag settlement pairs whose buffers overlap at all (potential ambiguity
    # even before any point landed in the overlap) for the QGIS reviewer
    s_coords = np.c_[settlements.geometry.x, settlements.geometry.y]
    tree = cKDTree(s_coords)
    pairs = tree.query_pairs(r=2 * base_radius.max())
    overlap_flag = np.zeros(len(settlements), dtype=bool)
    for i, j in pairs:
        d = np.hypot(*(s_coords[i] - s_coords[j]))
        if d < (base_radius[i] + base_radius[j]):
            overlap_flag[i] = True
            overlap_flag[j] = True
    settlements["buffer_overlaps_neighbor"] = overlap_flag

    return settlements


def sensitivity_sweep(tracks: gpd.GeoDataFrame, settlements: gpd.GeoDataFrame) -> pd.DataFrame:
    s_coords = np.c_[settlements.geometry.x, settlements.geometry.y]
    t_coords = np.c_[tracks.geometry.x, tracks.geometry.y]
    s_tree = cKDTree(s_coords)
    rows = []
    for r in SENSITIVITY_RADII_M:
        candidate_lists = s_tree.query_ball_point(t_coords, r=r)
        visited_idx = set()
        for cand in candidate_lists:
            visited_idx.update(cand)
        n_visited = len(visited_idx)
        rows.append(
            {
                "fixed_radius_m": r,
                "n_settlements_visited": n_visited,
                "n_settlements_not_visited": len(settlements) - n_visited,
                "pct_visited": round(100 * n_visited / len(settlements), 1),
            }
        )
    return pd.DataFrame(rows)


def main():
    print("[attribute] loading cleaned_tracks and settlement masterlist ...")
    tracks = gpd.read_file(GPKG, layer="cleaned_tracks").to_crs(PROJ_CRS)
    tracks["timestamp"] = pd.to_datetime(tracks["timestamp"])
    settlements = load_settlements()
    inaccessible = load_inaccessible()
    print(f"[attribute] {len(tracks)} cleaned track points, {len(settlements)} planned settlements")

    base_radius, nn_dist = compute_base_radius(settlements)
    print(
        f"[attribute] base radius (0.4 x NN dist, clipped {BASE_RADIUS_MIN_M}-{BASE_RADIUS_MAX_M}m): "
        f"min={base_radius.min():.0f}m median={np.median(base_radius):.0f}m max={base_radius.max():.0f}m"
    )

    matches = attribute(tracks, settlements, base_radius)
    print(f"[attribute] {len(matches)} point-settlement matches within tolerance")

    visits = build_settlement_visits(settlements, base_radius, nn_dist, matches, inaccessible)
    n_visited = int(visits["track_visited"].sum())
    print(f"[attribute] {n_visited} of {len(visits)} settlements track-confirmed visited "
          f"({100*n_visited/len(visits):.1f}%)")
    print(f"[attribute] {int(visits['any_ambiguous_match'].sum())} settlements received at least one "
          f"ambiguously-shared point match")
    print(f"[attribute] {int(visits['buffer_overlaps_neighbor'].sum())} settlements have a buffer "
          f"overlapping a neighbor's buffer (structural ambiguity risk)")

    sens = sensitivity_sweep(tracks, settlements)
    print("[attribute] buffer radius sensitivity sweep:")
    print(sens.to_string(index=False))
    sens.to_csv(REPO_ROOT / "outputs" / "buffer_sensitivity.csv", index=False)

    # write outputs (back to EPSG:4326 for the GeoPackage, QGIS-friendly)
    visits_out = visits.to_crs("EPSG:4326").copy()
    visits_out["first_visit_ts"] = visits_out["first_visit_ts"].astype(str)
    visits_out["last_visit_ts"] = visits_out["last_visit_ts"].astype(str)
    visits_out.to_file(GPKG, layer="settlement_visits", driver="GPKG", mode="w")
    print(f"[attribute] wrote settlement_visits layer: {len(visits_out)} rows")

    match_out = matches.merge(
        settlements[["settlement_id", "settlement_name", "ward_name", "lga_name"]], on="settlement_id", how="left"
    )
    match_out["timestamp"] = match_out["timestamp"].astype(str)
    match_out.drop(columns=["settlement_idx"]).to_csv(
        REPO_ROOT / "outputs" / "track_settlement_matches.csv", index=False
    )
    print(f"[attribute] wrote outputs/track_settlement_matches.csv: {len(match_out)} rows")


if __name__ == "__main__":
    main()
