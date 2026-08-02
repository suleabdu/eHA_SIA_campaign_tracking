"""
02_qc_tracks.py -- documented QA rule set applied to raw_tracks.

Every threshold in this file is derived from an inspection of this dataset's
own distributions (see docs/METHODOLOGY.md, section 2, for the evidence
behind each number) rather than picked arbitrarily. Rules are applied in a
fixed order because later rules are only meaningful/interpretable once
earlier ones have run (e.g. speed cannot be judged sensibly on a sequence
that still contains rows from the wrong calendar day).

Rule order
----------
QA-1  Source-day provenance filter     (removal)
QA-2a Null-island coordinate filter    (removal)
QA-2b Lat/lon transposition correction (correction, not removal)
QA-3  Implausible speed / kinematic outlier filter   (removal -> quarantine)
QA-4  Duty-hours window filter          (removal -> quarantine)
QA-5  Residual duplicate-timestamp identity conflicts (removal -> quarantine)
QA-6  Positional accuracy flag          (flag only, not removed)
QA-7  Fix-sequence gap detection        (flag only, event table)
QA-8  Stationary cluster detection      (flag only, event table)

Outputs
--------------------------------------------------
Two GeoPackages are used deliberately, not one -- see README.md "Outputs
and what's committed" and docs/METHODOLOGY.md sec.1:

  outputs/bansara_sia.gpkg (small, ~26MB, committed to the repo)
    cleaned_tracks        -- rows surviving QA-1..QA-5, with QA-6 accuracy
                              flag attached as an attribute (used by every
                              downstream stage)
    stationary_events      -- QA-8 output, one row per detected stationary
                              dwell

  outputs/bansara_sia_audit.gpkg (large, ~467MB, gitignored, rebuilt by
  running 01_ingest.py then this script)
    raw_tracks              -- written by 01_ingest.py; every row from every
                              source file, unfiltered
    quarantined_tracks       -- every row removed by QA-1 (see note below),
                              QA-2a, QA-3, QA-4, QA-5, with a `qc_reason`
                              column

outputs/gap_events.csv (QA-7, one row per detected fix-sequence gap) is
small and committed regardless of which GeoPackage layers are.

Because QA-1 alone removes 818,397 of 956,702 rows, writing every one of
those rows into `quarantined_tracks` individually would make the GeoPackage
unwieldy without adding audit value (the reason is identical and mechanical
for all of them: source-day mismatch). QA-1 removals are therefore written
to quarantined_tracks in full (so nothing is silently discarded -- the
brief is explicit that unexplained records must not be deleted) but the QA
report separately calls out that this is overwhelmingly one root cause, not
818,397 independent anomalies.

Console output prints a per-rule removal/flag count, which is also written
to outputs/qa_report.md by 06_make_maps.py's report builder (kept in a
single place to avoid drift between the printed numbers and the shipped
report).
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

REPO_ROOT = Path(__file__).resolve().parents[1]
GPKG = REPO_ROOT / "outputs" / "bansara_sia.gpkg"          # small, committed deliverable
AUDIT_GPKG = REPO_ROOT / "outputs" / "bansara_sia_audit.gpkg"  # large, gitignored audit trail
STATE_GPKG = REPO_ROOT / "data" / "raw" / "boundaries.gpkg"

# ---------------------------------------------------------------------------
# Thresholds -- see docs/METHODOLOGY.md sec.2 for the empirical justification
# behind every one of these.
# ---------------------------------------------------------------------------
SPEED_LIMIT_KMH = 15.0          # walking-pace data sits at 0-6 km/h with a
                                  # clean empirical gap up to >100 km/h; 15 km/h
                                  # gives headroom for brisk walking / jitter
                                  # while excluding every observed spike.
DUTY_START = pd.Timedelta(hours=7, minutes=0)
DUTY_END = pd.Timedelta(hours=18, minutes=30)   # local sunset ~18:15-18:30 at
                                                   # this latitude in March;
                                                   # house-to-house work is not
                                                   # operationally plausible
                                                   # after dark.
ACCURACY_DEGRADED_M = 30.0      # above this, treat the fix as low-confidence
                                  # for settlement attribution purposes (flag
                                  # only -- see Q1.3 adaptive buffer).
ACCURACY_HARD_CEILING_M = 100.0  # defensive ceiling; 0 rows exceed this in
                                  # this campaign, kept for robustness on
                                  # future rounds.
GAP_THRESHOLD_MIN = 5.0          # >=4 consecutively missed fixes at the
                                  # ~60s duty cycle stated in the data pack.
STATIONARY_SPEED_KMH = 1.0
STATIONARY_MIN_DURATION_MIN = 20.0  # see docs/METHODOLOGY.md: dwell duration
                                      # distribution has a natural break here.

EARTH_R_M = 6371000.0


def haversine_m(lon1, lat1, lon2, lat2):
    lon1, lat1, lon2, lat2 = map(np.radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_R_M * np.arcsin(np.sqrt(a))


def load_raw() -> gpd.GeoDataFrame:
    gdf = gpd.read_file(AUDIT_GPKG, layer="raw_tracks")
    gdf["timestamp"] = pd.to_datetime(gdf["timestamp"])
    return gdf


def qa1_provenance_filter(gdf: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    ts_date = gdf["timestamp"].dt.strftime("%Y-%m-%d")
    keep_mask = ts_date == gdf["file_label_date"]
    kept = gdf[keep_mask].copy()
    removed = gdf[~keep_mask].copy()
    removed["qc_reason"] = "QA1_source_day_mismatch"
    return kept, removed


def qa2_coordinate_sanity(gdf: gpd.GeoDataFrame, state_geom) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]:
    x, y = gdf.geometry.x.values, gdf.geometry.y.values
    null_island = (x == 0) & (y == 0)

    within_state = gdf.geometry.within(state_geom)
    swapped_geom = gpd.GeoSeries(gpd.points_from_xy(y, x), crs=gdf.crs, index=gdf.index)
    swap_fixable = (~within_state) & (~null_island) & swapped_geom.within(state_geom)

    corrected = gdf.copy()
    corrected.loc[swap_fixable, "longitude"] = y[swap_fixable.values]
    corrected.loc[swap_fixable, "latitude"] = x[swap_fixable.values]
    corrected.loc[swap_fixable, "geometry"] = swapped_geom[swap_fixable]
    corrected["coord_swap_corrected"] = swap_fixable

    kept = corrected[~null_island].copy()
    removed_null_island = corrected[null_island].copy()
    removed_null_island["qc_reason"] = "QA2a_null_island_coordinate"

    swap_log = corrected[swap_fixable][
        ["row_uid", "team_id", "timestamp", "source_file"]
    ].copy()
    return kept, removed_null_island, swap_log


def compute_geometric_speed(gdf: gpd.GeoDataFrame) -> pd.Series:
    """Recompute speed from consecutive-fix geometry within each team/day
    sequence, independent of the logger-reported speed_kmh field. Used as a
    cross-check: QA-3 flags a point if EITHER the reported speed OR the
    geometry-implied speed (to the previous fix or from it to the next fix)
    exceeds the limit, since a single erroneous fix produces two implausible
    legs (in and out) and the logger's own reported speed does not always
    reflect it (see docs/METHODOLOGY.md sec.2.3)."""
    g = gdf.sort_values(["team_id", "file_label_date", "timestamp"]).copy()
    grp = g.groupby(["team_id", "file_label_date"])
    dt_h = grp["timestamp"].diff().dt.total_seconds() / 3600.0
    dist_m = haversine_m(
        grp["longitude"].shift(), grp["latitude"].shift(), g["longitude"], g["latitude"]
    )
    speed_in = (dist_m / 1000.0) / dt_h
    speed_out = speed_in.groupby([g["team_id"], g["file_label_date"]]).shift(-1)
    implied = pd.concat([speed_in, speed_out], axis=1).max(axis=1)
    return implied.reindex(gdf.index)


def qa3_speed_filter(gdf: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    implied_speed = compute_geometric_speed(gdf)
    is_implausible = (gdf["speed_kmh"] > SPEED_LIMIT_KMH) | (implied_speed > SPEED_LIMIT_KMH)
    kept = gdf[~is_implausible].copy()
    removed = gdf[is_implausible].copy()
    removed["qc_reason"] = "QA3_implausible_speed"
    return kept, removed


def qa4_duty_hours_filter(gdf: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    tod = gdf["timestamp"] - gdf["timestamp"].dt.normalize()
    keep_mask = (tod >= DUTY_START) & (tod <= DUTY_END)
    kept = gdf[keep_mask].copy()
    removed = gdf[~keep_mask].copy()
    removed["qc_reason"] = "QA4_outside_duty_hours"
    return kept, removed


def qa5_residual_duplicates(gdf: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    dup_mask = gdf.duplicated(subset=["team_id", "timestamp"], keep=False)
    kept = gdf[~dup_mask].copy()
    removed = gdf[dup_mask].copy()
    removed["qc_reason"] = "QA5_unresolved_identity_conflict"
    return kept, removed


def qa6_accuracy_flag(gdf: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Flag only -- does not remove. Returns (gdf_with_flag, hard_ceiling_violations)."""
    gdf = gdf.copy()
    gdf["accuracy_degraded"] = gdf["accuracy_m"].isna() | (gdf["accuracy_m"] > ACCURACY_DEGRADED_M)
    hard_violation = gdf["accuracy_m"] > ACCURACY_HARD_CEILING_M
    violations = gdf[hard_violation].copy()
    violations["qc_reason"] = "QA6_accuracy_hard_ceiling_exceeded"
    return gdf, violations


def qa7_gap_events(gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    g = gdf.sort_values(["team_id", "file_label_date", "timestamp"]).copy()
    grp = g.groupby(["team_id", "file_label_date"])
    g["prev_ts"] = grp["timestamp"].shift(1)
    g["gap_min"] = (g["timestamp"] - g["prev_ts"]).dt.total_seconds() / 60.0
    events = g[g["gap_min"] >= GAP_THRESHOLD_MIN][
        ["team_id", "file_label_date", "prev_ts", "timestamp", "gap_min"]
    ].rename(columns={"timestamp": "resumed_at", "prev_ts": "gap_started_after"})
    return events.reset_index(drop=True)


def qa8_stationary_events(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    g = gdf.sort_values(["team_id", "file_label_date", "timestamp"]).copy().reset_index(drop=True)
    g["still"] = g["speed_kmh"] < STATIONARY_SPEED_KMH
    key = g["team_id"].astype(str) + "|" + g["file_label_date"].astype(str)
    change = (g["still"] != g["still"].shift()) | (key != key.shift())
    g["run_id"] = change.cumsum()

    stillruns = g[g["still"]].groupby("run_id")
    rows = []
    for run_id, sub in stillruns:
        if len(sub) < 2:
            continue
        dur_min = (sub["timestamp"].max() - sub["timestamp"].min()).total_seconds() / 60.0
        if dur_min >= STATIONARY_MIN_DURATION_MIN:
            rows.append(
                {
                    "team_id": sub["team_id"].iloc[0],
                    "file_label_date": sub["file_label_date"].iloc[0],
                    "start_ts": sub["timestamp"].min(),
                    "end_ts": sub["timestamp"].max(),
                    "duration_min": dur_min,
                    "n_points": len(sub),
                    "centroid_lon": sub["longitude"].mean(),
                    "centroid_lat": sub["latitude"].mean(),
                }
            )
    out = pd.DataFrame(rows)
    if out.empty:
        return gpd.GeoDataFrame(out, geometry=[], crs="EPSG:4326")
    gdf_out = gpd.GeoDataFrame(
        out, geometry=gpd.points_from_xy(out["centroid_lon"], out["centroid_lat"]), crs="EPSG:4326"
    )
    return gdf_out


def main():
    print("[qc] loading raw_tracks ...")
    raw = load_raw()
    n0 = len(raw)
    print(f"[qc] raw_tracks: {n0} rows")

    state = gpd.read_file(STATE_GPKG, layer="state")
    state_geom = state.union_all().buffer(0.01)  # ~1.1km grace margin for
    # boundary-adjacent settlements / coarse polygon vertices; see METHODOLOGY.

    quarantine_frames = []

    kept, rem1 = qa1_provenance_filter(raw)
    quarantine_frames.append(rem1)
    print(f"[qc] QA-1 source-day provenance filter: removed {len(rem1)}, kept {len(kept)}")

    kept, rem2a, swap_log = qa2_coordinate_sanity(kept, state_geom)
    quarantine_frames.append(rem2a)
    print(f"[qc] QA-2a null-island coordinate filter: removed {len(rem2a)}, kept {len(kept)}")
    print(f"[qc] QA-2b lat/lon transposition correction: corrected {len(swap_log)} rows in place (not removed)")

    kept, rem3 = qa3_speed_filter(kept)
    quarantine_frames.append(rem3)
    print(f"[qc] QA-3 implausible speed filter (>{SPEED_LIMIT_KMH} km/h): removed {len(rem3)}, kept {len(kept)}")

    kept, rem4 = qa4_duty_hours_filter(kept)
    quarantine_frames.append(rem4)
    print(f"[qc] QA-4 duty-hours filter ({DUTY_START} - {DUTY_END}): removed {len(rem4)}, kept {len(kept)}")

    kept, rem5 = qa5_residual_duplicates(kept)
    quarantine_frames.append(rem5)
    print(f"[qc] QA-5 residual identity-conflict duplicates: removed {len(rem5)}, kept {len(kept)}")

    kept, rem6 = qa6_accuracy_flag(kept)
    print(
        f"[qc] QA-6 accuracy flag (>{ACCURACY_DEGRADED_M}m or missing): "
        f"{int(kept['accuracy_degraded'].sum())} of {len(kept)} flagged (NOT removed); "
        f"{len(rem6)} exceeded the {ACCURACY_HARD_CEILING_M}m hard ceiling"
    )

    gaps = qa7_gap_events(kept)
    print(f"[qc] QA-7 fix-sequence gaps (>= {GAP_THRESHOLD_MIN} min): {len(gaps)} events flagged")

    stationary = qa8_stationary_events(kept)
    print(f"[qc] QA-8 stationary clusters (>= {STATIONARY_MIN_DURATION_MIN} min, <{STATIONARY_SPEED_KMH} km/h): {len(stationary)} events flagged")

    # ---- write outputs ----
    keep_cols = [
        "row_uid", "team_id", "logger_id", "timestamp", "longitude", "latitude",
        "accuracy_m", "speed_kmh", "source_file", "file_label_team", "file_label_date",
        "coord_swap_corrected", "accuracy_degraded", "geometry",
    ]
    cleaned_out = kept[keep_cols].copy()
    cleaned_out["timestamp"] = cleaned_out["timestamp"].astype(str)
    cleaned_out.to_file(GPKG, layer="cleaned_tracks", driver="GPKG", mode="w")
    print(f"[qc] wrote cleaned_tracks: {len(cleaned_out)} rows")

    quarantine_all = pd.concat(quarantine_frames, ignore_index=False)
    q_cols = [
        "row_uid", "team_id", "logger_id", "timestamp", "longitude", "latitude",
        "accuracy_m", "speed_kmh", "source_file", "file_label_team", "file_label_date",
        "qc_reason", "geometry",
    ]
    quarantine_out = gpd.GeoDataFrame(quarantine_all[q_cols], geometry="geometry", crs=raw.crs)
    quarantine_out["timestamp"] = quarantine_out["timestamp"].astype(str)
    quarantine_out.to_file(AUDIT_GPKG, layer="quarantined_tracks", driver="GPKG", mode="w")
    print(f"[qc] wrote quarantined_tracks to {AUDIT_GPKG.name}: {len(quarantine_out)} rows")

    if not gaps.empty:
        gaps_out = gaps.copy()
        gaps_out["gap_started_after"] = gaps_out["gap_started_after"].astype(str)
        gaps_out["resumed_at"] = gaps_out["resumed_at"].astype(str)
        gaps_out.to_csv(REPO_ROOT / "outputs" / "gap_events.csv", index=False)
        print(f"[qc] wrote outputs/gap_events.csv: {len(gaps_out)} rows")

    if len(stationary):
        stat_out = stationary.copy()
        stat_out["start_ts"] = stat_out["start_ts"].astype(str)
        stat_out["end_ts"] = stat_out["end_ts"].astype(str)
        stat_out.to_file(GPKG, layer="stationary_events", driver="GPKG", mode="w")
        print(f"[qc] wrote stationary_events layer: {len(stat_out)} rows")

    # ---- per-rule / per-team summary for the QA report ----
    summary_rows = [
        {"rule": "QA1_source_day_mismatch", "action": "removed", "n": len(rem1)},
        {"rule": "QA2a_null_island_coordinate", "action": "removed", "n": len(rem2a)},
        {"rule": "QA2b_coord_swap_corrected", "action": "corrected", "n": len(swap_log)},
        {"rule": "QA3_implausible_speed", "action": "removed", "n": len(rem3)},
        {"rule": "QA4_outside_duty_hours", "action": "removed", "n": len(rem4)},
        {"rule": "QA5_unresolved_identity_conflict", "action": "removed", "n": len(rem5)},
        {"rule": "QA6_accuracy_degraded_flag", "action": "flagged_not_removed", "n": int(kept["accuracy_degraded"].sum())},
        {"rule": "QA6_accuracy_hard_ceiling", "action": "removed", "n": len(rem6)},
        {"rule": "QA7_gap_events", "action": "flagged_event", "n": len(gaps)},
        {"rule": "QA8_stationary_events", "action": "flagged_event", "n": len(stationary)},
        {"rule": "FINAL_cleaned_tracks", "action": "retained", "n": len(cleaned_out)},
        {"rule": "RAW_total", "action": "input", "n": n0},
    ]
    summary_df = pd.DataFrame(summary_rows)
    summary_df.to_csv(REPO_ROOT / "outputs" / "qa_rule_summary.csv", index=False)
    print("\n[qc] === Rule summary ===")
    print(summary_df.to_string(index=False))

    per_team = (
        pd.concat(
            [f.assign(qc_reason=f["qc_reason"]) for f in quarantine_frames], ignore_index=True
        )
        .groupby(["team_id", "qc_reason"])
        .size()
        .unstack(fill_value=0)
    )
    per_team.to_csv(REPO_ROOT / "outputs" / "qa_removed_by_team.csv")
    print(f"[qc] wrote outputs/qa_removed_by_team.csv ({per_team.shape[0]} teams x {per_team.shape[1]} rules)")


if __name__ == "__main__":
    main()
