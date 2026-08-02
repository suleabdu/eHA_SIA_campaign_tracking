"""
01_ingest.py -- idempotent ingestion of raw GPS track files into a single
spatially-enabled GeoPackage store.

Usage
-----
    python src/01_ingest.py
    python src/01_ingest.py --run twice   # runs ingestion twice back-to-back
                                            # and asserts row counts match,
                                            # as a self-contained proof of
                                            # idempotency.

What this does
---------------
1. Globs every file in data/raw/tracks/*.csv (160 files, one per team per day).
2. Content-hashes each file (SHA-256) and checks it against a manifest table
   already stored inside the output GeoPackage. Files already ingested (same
   name + same hash) are skipped.
3. For new files: parses the CSV, builds Point geometries in EPSG:4326 (the
   CRS the raw coordinates are supplied in -- see docs/METHODOLOGY.md for why
   no reprojection happens at this stage), attaches provenance columns, and
   appends to the `raw_tracks` layer.
4. Every row also gets a deterministic `row_uid` (sha1 of
   source_file|row_index|team_id|timestamp) so any row can be traced back to
   an exact line in an exact source file later in the pipeline.

Provenance columns added (not present in the raw CSVs):
    source_file        -- relative path of the file the row came from
    file_label_team     -- team id parsed from the filename
    file_label_date     -- calendar date parsed from the filename
    ingest_batch_id      -- id of the ingestion run that loaded this row
    row_uid              -- deterministic per-row identifier

This stage does **no** quality filtering. Every row in every source file is
loaded, including rows that are later found to be defective. That triage
happens in 02_qc_tracks.py, deliberately kept separate so the raw layer
remains a faithful, complete copy of what the field actually returned.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import uuid
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.gpkg_store import (  # noqa: E402
    already_ingested,
    ensure_manifest_table,
    record_manifest,
    sha256_of_file,
    write_or_append,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TRACKS_DIR = REPO_ROOT / "data" / "raw" / "tracks"
# Full-fidelity audit layers (raw_tracks here, quarantined_tracks in
# 02_qc_tracks.py) go in a SEPARATE GeoPackage from the analysis-ready
# deliverable. raw_tracks alone is ~240MB at 956,702 rows -- comfortably
# over GitHub's 100MB hard per-file push limit -- so it is regenerated
# locally by running this script rather than committed. See README.md
# "Outputs and what's committed" and docs/METHODOLOGY.md sec.1.
AUDIT_GPKG = REPO_ROOT / "outputs" / "bansara_sia_audit.gpkg"
OUT_GPKG = AUDIT_GPKG
RAW_LAYER = "raw_tracks"

FILENAME_RE = re.compile(r"^(?P<team>T\d+)_(?P<date>\d{4}-\d{2}-\d{2})\.csv$")

EXPECTED_COLUMNS = [
    "team_id",
    "logger_id",
    "timestamp",
    "longitude",
    "latitude",
    "accuracy_m",
    "speed_kmh",
]


def row_uid(source_file: str, row_idx: int, team_id: str, ts: str) -> str:
    payload = f"{source_file}|{row_idx}|{team_id}|{ts}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def load_one_file(path: Path, batch_id: str) -> gpd.GeoDataFrame:
    m = FILENAME_RE.match(path.name)
    if not m:
        raise ValueError(f"Filename does not match expected <team>_<date>.csv pattern: {path.name}")
    file_label_team = m.group("team")
    file_label_date = m.group("date")

    df = pd.read_csv(path, dtype={"team_id": str, "logger_id": str})
    missing = set(EXPECTED_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"{path.name} is missing expected columns: {missing}")

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="raise")
    df["source_file"] = str(path.relative_to(REPO_ROOT))
    df["file_label_team"] = file_label_team
    df["file_label_date"] = file_label_date
    df["ingest_batch_id"] = batch_id
    df["row_uid"] = [
        row_uid(df["source_file"].iloc[i], i, df["team_id"].iloc[i], str(df["timestamp"].iloc[i]))
        for i in range(len(df))
    ]

    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["longitude"], df["latitude"]),
        crs="EPSG:4326",
    )
    return gdf


def run_ingest() -> dict:
    OUT_GPKG.parent.mkdir(parents=True, exist_ok=True)
    # NOTE: the manifest table is deliberately NOT created here if the GeoPackage
    # doesn't exist yet. GDAL/OGR initialises GPKG-required metadata tables
    # (gpkg_contents, gpkg_spatial_ref_sys, etc.) the first time a spatial layer
    # is written via geopandas/pyogrio. Pre-creating a bare sqlite3 file at this
    # path first (as an earlier version of this script did) leaves a file GDAL
    # then treats as a corrupt GeoPackage ("bad application_id"). The manifest
    # table is created (idempotently) just after the first spatial write below,
    # once a valid GeoPackage is guaranteed to exist.
    if OUT_GPKG.exists():
        ensure_manifest_table(OUT_GPKG)

    files = sorted(TRACKS_DIR.glob("*.csv"))
    if not files:
        raise SystemExit(f"No track files found under {TRACKS_DIR}")

    batch_id = uuid.uuid4().hex[:12]
    n_loaded_files = 0
    n_skipped_files = 0
    n_rows_appended = 0

    for path in files:
        digest = sha256_of_file(path)
        rel = str(path.relative_to(REPO_ROOT))
        if already_ingested(OUT_GPKG, rel, digest):
            n_skipped_files += 1
            continue

        gdf = load_one_file(path, batch_id)
        write_or_append(gdf, OUT_GPKG, RAW_LAYER)
        ensure_manifest_table(OUT_GPKG)  # idempotent; guaranteed valid GPKG exists now
        record_manifest(OUT_GPKG, rel, digest, len(gdf), batch_id)
        n_loaded_files += 1
        n_rows_appended += len(gdf)

    total_rows = len(gpd.read_file(OUT_GPKG, layer=RAW_LAYER)) if _layer_present() else 0

    summary = {
        "files_seen": len(files),
        "files_newly_loaded": n_loaded_files,
        "files_skipped_already_ingested": n_skipped_files,
        "rows_appended_this_run": n_rows_appended,
        "total_rows_in_raw_tracks": total_rows,
    }
    return summary


def _layer_present() -> bool:
    try:
        layers = gpd.list_layers(OUT_GPKG)
        return RAW_LAYER in set(layers["name"])
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--run",
        choices=["once", "twice"],
        default="once",
        help="'twice' runs ingestion twice in a row and asserts idempotency (row counts identical).",
    )
    ap.add_argument(
        "--fresh",
        action="store_true",
        help="Delete any existing output GeoPackage first (clean-room run).",
    )
    args = ap.parse_args()

    if args.fresh and OUT_GPKG.exists():
        OUT_GPKG.unlink()
        print(f"[ingest] removed existing {OUT_GPKG} for a clean-room run")

    print("[ingest] === run 1 ===")
    s1 = run_ingest()
    for k, v in s1.items():
        print(f"[ingest]   {k}: {v}")

    if args.run == "twice":
        print("[ingest] === run 2 (idempotency check) ===")
        s2 = run_ingest()
        for k, v in s2.items():
            print(f"[ingest]   {k}: {v}")
        assert s2["files_newly_loaded"] == 0, "Second run should not load any new files"
        assert s2["total_rows_in_raw_tracks"] == s1["total_rows_in_raw_tracks"], (
            "Row count changed between run 1 and run 2 -- ingestion is NOT idempotent"
        )
        print(
            f"[ingest] IDEMPOTENCY CONFIRMED: {s1['total_rows_in_raw_tracks']} rows "
            f"after run 1 and after run 2."
        )


if __name__ == "__main__":
    main()
