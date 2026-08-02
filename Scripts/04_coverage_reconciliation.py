"""
04_coverage_reconciliation.py -- settlement- and ward-level coverage from
GPS tracks, reconciled against the e-tally, with discrepancy causes
classified using ward-day data-availability evidence.

Core design decision: data availability as a first-class variable
-------------------------------------------------------------------
A settlement with zero track-confirmed visits and zero e-tally records is
NOT automatically "missed". Given QA removed 89.8% of raw GPS points (see
02_qc_tracks.py), a team could easily have visited a settlement on a day
when its own logger produced no usable fixes at all. Before any settlement
is called "missed", this pipeline checks whether there is *any* independent
evidence that campaign activity happened in that settlement's ward that
day -- either a surviving cleaned track point anywhere in the ward, or an
e-tally record anywhere in the ward. This is the `ward_day_active` signal.

Settlement classification logic (see docs/METHODOLOGY.md sec.4 for the
full decision table and worked examples):

  security_classification != "Accessible"
      -> "Planned exclusion (security)"                [not a coverage gap]

  track_visited == True
      -> "Visited (track-confirmed)"

  track_visited == False AND etally_reported == True
      -> "Tally-only (no GPS confirmation)"             [reporting/attribution
                                                          discrepancy, flagged]

  track_visited == False AND etally_reported == False AND
  ward had zero active days across the whole campaign
      -> "Indeterminate - no data available"            [data artefact, NOT
                                                          evidence of a miss]

  track_visited == False AND etally_reported == False AND
  ward had at least one active day
      -> "Plausible miss"                                [the operationally
                                                          meaningful category
                                                          for mop-up targeting
                                                          and Q1.5 clustering]

Outputs
-------
    settlement_coverage       -- one row per planned settlement with full
                                 classification (GeoPackage layer + CSV)
    ward_coverage              -- ward-level track vs e-tally coverage,
                                 discrepancy, and data-availability context
    lga_coverage                -- LGA roll-up of the same
    reconciliation_report.md   -- narrative summary with discrepancy causes
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

REPO_ROOT = Path(__file__).resolve().parents[1]
GPKG = REPO_ROOT / "outputs" / "bansara_sia.gpkg"
CAMPAIGN_DAYS = ["2026-03-09", "2026-03-10", "2026-03-11", "2026-03-12", "2026-03-13"]


def load_data():
    visits = gpd.read_file(GPKG, layer="settlement_visits")
    tracks = gpd.read_file(GPKG, layer="cleaned_tracks")
    tracks["timestamp"] = pd.to_datetime(tracks["timestamp"])
    tracks["date"] = tracks["timestamp"].dt.strftime("%Y-%m-%d")
    wards = gpd.read_file(REPO_ROOT / "data" / "raw" / "boundaries.gpkg", layer="wards")
    lgas = gpd.read_file(REPO_ROOT / "data" / "raw" / "boundaries.gpkg", layer="lgas")
    etally = pd.read_csv(
        REPO_ROOT / "data" / "raw" / "etally_daily.csv",
        dtype={"settlement_id": str, "team_id": str, "ward_code": str},
    )
    return visits, tracks, wards, lgas, etally


def compute_ward_day_active(tracks: gpd.GeoDataFrame, wards: gpd.GeoDataFrame, etally: pd.DataFrame) -> pd.DataFrame:
    tracks_ward = gpd.sjoin(tracks, wards[["ward_code", "geometry"]], how="left", predicate="within")
    track_active = tracks_ward.dropna(subset=["ward_code"]).groupby(["ward_code", "date"]).size()
    track_active_set = set(track_active.index)
    track_point_counts = tracks_ward.dropna(subset=["ward_code"]).groupby("ward_code").size()

    etally_active = etally.dropna(subset=["ward_code"]).groupby(["ward_code", "campaign_date"]).size()
    etally_active_set = set(etally_active.index)

    rows = []
    for _, w in wards.iterrows():
        wc = w["ward_code"]
        track_days = [d for d in CAMPAIGN_DAYS if (wc, d) in track_active_set]
        etally_days = [d for d in CAMPAIGN_DAYS if (wc, d) in etally_active_set]
        active_days = sorted(set(track_days) | set(etally_days))
        rows.append(
            {
                "ward_code": wc,
                "ward_name": w["ward_name"],
                "n_track_points_in_ward": int(track_point_counts.get(wc, 0)),
                "n_track_active_days": len(track_days),
                "n_etally_active_days": len(etally_days),
                "n_active_days": len(active_days),
                "active_days": ",".join(active_days),
                "ward_ever_active": len(active_days) > 0,
                "complete_gps_blackout": len(track_days) == 0,
            }
        )
    return pd.DataFrame(rows)


def build_settlement_coverage(visits: gpd.GeoDataFrame, etally: pd.DataFrame, ward_activity: pd.DataFrame) -> gpd.GeoDataFrame:
    et_summary = (
        etally.groupby("settlement_id")
        .agg(
            etally_doses_total=("doses_administered", "sum"),
            etally_target_pop=("target_population_under5", "first"),
            etally_n_days_reported=("campaign_date", "nunique"),
            etally_teams=("team_id", lambda s: ",".join(sorted(set(s)))),
        )
        .reset_index()
    )

    cov = visits.merge(et_summary, on="settlement_id", how="left")
    cov["etally_reported"] = cov["etally_doses_total"].notna()
    cov["etally_doses_total"] = cov["etally_doses_total"].fillna(0).astype(int)

    cov = cov.merge(
        ward_activity[["ward_code", "n_active_days", "ward_ever_active", "complete_gps_blackout"]],
        on="ward_code", how="left"
    )

    def classify(row):
        if row["security_classification"] != "Accessible":
            return "Planned exclusion (security)"
        if row["track_visited"]:
            return "Visited (track-confirmed)"
        if row["etally_reported"]:
            return "Tally-only (no GPS confirmation)"
        if row["complete_gps_blackout"] or not row["ward_ever_active"]:
            return "Indeterminate - no data available"
        return "Plausible miss"

    cov["coverage_status"] = cov.apply(classify, axis=1)

    # dose coverage ratio where a target population figure exists (own masterlist
    # value; falls back to etally-reported target where the masterlist is missing
    # it -- see docs/METHODOLOGY.md sec.4 note on the 14 rows where the two
    # sources disagree)
    target = cov["target_population_under5"].fillna(cov.get("etally_target_pop"))
    with np.errstate(divide="ignore", invalid="ignore"):
        cov["dose_coverage_ratio"] = np.where(
            (target.notna()) & (target > 0), cov["etally_doses_total"] / target, np.nan
        )

    return cov


def ward_level_coverage(cov: gpd.GeoDataFrame, ward_activity: pd.DataFrame) -> pd.DataFrame:
    accessible = cov[cov["security_classification"] == "Accessible"]

    def agg(g):
        n_planned = len(g)
        n_track_visited = int(g["track_visited"].sum())
        n_etally_reported = int(g["etally_reported"].sum())
        n_any_evidence = int(((g["track_visited"]) | (g["etally_reported"])).sum())
        n_plausible_miss = int((g["coverage_status"] == "Plausible miss").sum())
        n_indeterminate = int((g["coverage_status"] == "Indeterminate - no data available").sum())
        doses = g["etally_doses_total"].sum()
        target = g["target_population_under5"].sum()
        return pd.Series(
            {
                "n_planned_accessible_settlements": n_planned,
                "n_track_visited": n_track_visited,
                "n_etally_reported": n_etally_reported,
                "n_any_evidence_visited": n_any_evidence,
                "n_plausible_miss": n_plausible_miss,
                "n_indeterminate_no_data": n_indeterminate,
                "track_coverage_pct": round(100 * n_track_visited / n_planned, 1) if n_planned else np.nan,
                "etally_settlement_coverage_pct": round(100 * n_etally_reported / n_planned, 1) if n_planned else np.nan,
                "any_evidence_coverage_pct": round(100 * n_any_evidence / n_planned, 1) if n_planned else np.nan,
                "etally_dose_coverage_pct": round(100 * doses / target, 1) if target > 0 else np.nan,
            }
        )

    ward_cov = accessible.groupby(["ward_code", "ward_name", "lga_name"]).apply(agg, include_groups=False).reset_index()
    ward_cov = ward_cov.merge(
        ward_activity[["ward_code", "n_active_days", "n_track_active_days", "n_etally_active_days", "complete_gps_blackout"]],
        on="ward_code", how="left"
    )
    ward_cov["track_etally_discrepancy_pp"] = (
        ward_cov["etally_settlement_coverage_pct"] - ward_cov["track_coverage_pct"]
    ).round(1)

    def discrepancy_cause(row):
        if row["complete_gps_blackout"]:
            return "Complete GPS blackout -- zero surviving track points anywhere in this ward across all 5 days despite e-tally activity; treat track-derived coverage as unavailable, not zero"
        if row["n_track_active_days"] <= 1 and row["track_etally_discrepancy_pp"] > 15:
            return "Likely GPS/logger data-availability failure (ward had almost no surviving track data)"
        if row["track_etally_discrepancy_pp"] > 25:
            return "Track coverage well below tally -- investigate attribution tolerance and logger uptime"
        if row["track_etally_discrepancy_pp"] < -15:
            return "Track coverage exceeds tally -- possible under-reporting or unlogged/incomplete e-tally entries"
        return "Sources broadly consistent"

    ward_cov["plausible_discrepancy_cause"] = ward_cov.apply(discrepancy_cause, axis=1)
    return ward_cov


def lga_level_coverage(ward_cov: pd.DataFrame, cov: gpd.GeoDataFrame) -> pd.DataFrame:
    accessible = cov[cov["security_classification"] == "Accessible"]

    def agg(g):
        n_planned = len(g)
        doses = g["etally_doses_total"].sum()
        target = g["target_population_under5"].sum()
        return pd.Series(
            {
                "n_planned_accessible_settlements": n_planned,
                "n_track_visited": int(g["track_visited"].sum()),
                "n_etally_reported": int(g["etally_reported"].sum()),
                "n_plausible_miss": int((g["coverage_status"] == "Plausible miss").sum()),
                "track_coverage_pct": round(100 * g["track_visited"].sum() / n_planned, 1),
                "etally_settlement_coverage_pct": round(100 * g["etally_reported"].sum() / n_planned, 1),
                "etally_dose_coverage_pct": round(100 * doses / target, 1) if target > 0 else np.nan,
            }
        )

    return accessible.groupby("lga_name").apply(agg, include_groups=False).reset_index()


def main():
    print("[reconcile] loading settlement_visits, cleaned_tracks, boundaries, etally ...")
    visits, tracks, wards, lgas, etally = load_data()

    off_list = set(etally["settlement_id"]) - set(visits["settlement_id"])
    print(f"[reconcile] {len(off_list)} e-tally settlement_ids not in the masterlist "
          f"(off-microplan visits): {sorted(off_list)}")

    ward_activity = compute_ward_day_active(tracks, wards, etally)
    never_active = ward_activity[~ward_activity["ward_ever_active"]]
    blackout = ward_activity[ward_activity["complete_gps_blackout"]]
    print(f"[reconcile] {len(never_active)} of {len(ward_activity)} wards show ZERO active days "
          f"(no track AND no e-tally evidence) across the whole 5-day campaign")
    print(f"[reconcile] {len(blackout)} of {len(ward_activity)} wards show a COMPLETE GPS BLACKOUT "
          f"(zero surviving track points anywhere in the ward, though e-tally shows activity):")
    if len(blackout):
        print(blackout[["ward_code", "ward_name", "n_etally_active_days"]].to_string(index=False))

    cov = build_settlement_coverage(visits, etally, ward_activity)
    print("\n[reconcile] settlement coverage_status counts:")
    print(cov["coverage_status"].value_counts().to_string())

    ward_cov = ward_level_coverage(cov, ward_activity)
    lga_cov = lga_level_coverage(ward_cov, cov)

    print("\n[reconcile] LGA-level coverage:")
    print(lga_cov.to_string(index=False))

    # ---- write outputs ----
    cov_out = cov.copy()
    for c in ["first_visit_ts", "last_visit_ts"]:
        if c in cov_out.columns:
            cov_out[c] = cov_out[c].astype(str)
    cov_out.to_file(GPKG, layer="settlement_coverage", driver="GPKG", mode="w")
    cov_out.drop(columns="geometry").to_csv(REPO_ROOT / "outputs" / "settlement_coverage.csv", index=False)
    print(f"\n[reconcile] wrote settlement_coverage layer + CSV: {len(cov_out)} rows")

    ward_cov.to_csv(REPO_ROOT / "outputs" / "ward_coverage.csv", index=False)
    ward_geom = wards.merge(ward_cov, on=["ward_code", "ward_name"], how="left")
    ward_geom.to_file(GPKG, layer="ward_coverage", driver="GPKG", mode="w")
    print(f"[reconcile] wrote ward_coverage layer + CSV: {len(ward_cov)} rows")

    lga_cov.to_csv(REPO_ROOT / "outputs" / "lga_coverage.csv", index=False)
    print(f"[reconcile] wrote outputs/lga_coverage.csv: {len(lga_cov)} rows")

    ward_activity.to_csv(REPO_ROOT / "outputs" / "ward_day_activity.csv", index=False)


if __name__ == "__main__":
    main()
