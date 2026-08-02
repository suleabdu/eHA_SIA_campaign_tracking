# Data merging across all CSV files in the folder
from pathlib import Path
import pandas as pd  # Step 1: Add flags
import numpy as np
from datetime import time
import glob
import pandas as pd
file_path = glob.glob("Data/raw/tracks/*.csv")
df_list = []
for file in file_path:
    df = pd.read_csv(file)
    df_list.append(df)
master_df = pd.concat(df_list, ignore_index=True)
master_df.to_csv("Data/intermediate/master_tracks.csv", index=False)


# timestamp threshold filtering

# file paths
input_file = Path("Data/intermediate/master_tracks.csv")
output_file = Path("Data/intermediate/cleaned_tracks_timestamp.csv")
dropped_path = Path("Data/intermediate/dropped_rows.csv")

# load the files
df = pd.read_csv(input_file)

# Parse timestamp with explicit format
df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')

# Report rows that failed to parse
failed_parsing = df['timestamp'].isna().sum()
if failed_parsing:
    print(
        f"Warning: {failed_parsing} rows failed to parse and will be dropped.")

# Define campaign days
campaign_dates = pd.date_range(start='2026-03-09', end='2026-03-13').date

# Define campaign start and end datetimes
start_time = time(7, 0, 0)  # 7:00 AM
end_time = time(18, 30, 0)  # 6:30 PM

# Filter directly by datetime range
date_mask = df['timestamp'].dt.date.isin(campaign_dates)
time_mask = df['timestamp'].dt.time.between(start_time, end_time)

# Sanity check: Ensure that the timestamp column is in datetime format
n_before = len(df)

# apply the masks to filter the DataFrame
df_filtered = df[date_mask & time_mask].copy()

n_after = len(df_filtered)
print(f"Rows before filtering: {n_before}")
print(f"Rows after filtering: {n_after} ({n_before - n_after} = dropped)")

# Save Cleaned data to output folder
df_filtered.to_csv(output_file, index=False)
dropped_path = Path("Data/intermediate/dropped_rows.csv")
# Save dropped rows to a separate CSV file
dropped_rows = df[~(date_mask & time_mask)]
dropped_rows.to_csv(dropped_path, index=False)
print(f"Dropped rows saved to {dropped_path}")
print(f"Filtered data saved to {output_file}")


# Conditionally flag rows based on accuracy and speed thresholds

df = pd.read_csv("Data/intermediate/master_tracks.csv")
# conditions
conditions = [
    df['accuracy_m'].isna() & df['speed_kmh'].isna(),
    df['accuracy_m'].isna(),
    df['speed_kmh'].isna(),
    df['accuracy_m'] > 10,
    df['speed_kmh'] > 6
]
# define message
messages = [
    'Missing accuracy and speed',
    'Missing accuracy',
    'Missing speed',
    'Accuracy exceeds threshold',
    'Speed exceeds threshold'
]
# Helper Column
df['flag'] = np.select(conditions, messages, default='Pass')
# Step 2: Save flagged dataset to intermediate folder
df.to_csv("Data/intermediate/Tracks_flag.csv", index=False)

# Step 3: Remove flagged rows (keep only those that pass thresholds)
df_cleaned = df[(df['flag'] == 'Pass')]

# Step 4: Save cleaned dataset to processed folder
df_cleaned.to_csv("Data/processed/cleaned_tracks.csv", index=False)

# Stnardize and clean settlement masterlist data

# Load data
df = pd.read_csv("Data/raw/settlement_masterlist.csv")

# --- Text column cleaning ---
text_cols = ['ward_name', 'settlement_name', 'settlement_type', 'ward_code']
for col in text_cols:
    if col in df.columns:
        df[col] = (
            df[col]
            .astype(str)
            .str.strip()
            .str.replace(r'\s+', ' ', regex=True)
        )
        hidden_char_mask = df[col].str.contains(
            r'[^\x20-\x7E]', regex=True, na=False)
        if hidden_char_mask.any():
            print(
                f"Warning: {hidden_char_mask.sum()} hidden/non-standard character(s) found in '{col}'.")
            df[col] = df[col].str.replace(r'[^\x20-\x7E]', '', regex=True)

        # Proper case for ward_name
        if col in ['ward_name', 'settlement_name', 'settlement_type']:
            df[col] = df[col].str.title()

# --- Coordinate validation ---
for coord_col, valid_range in [('latitude', (-90, 90)), ('longitude', (-180, 180))]:
    if coord_col in df.columns:
        df[coord_col] = pd.to_numeric(df[coord_col], errors='coerce')

        n_missing = df[coord_col].isna().sum()
        if n_missing:
            print(
                f"Warning: {n_missing} missing/non-numeric value(s) in '{coord_col}'.")

        out_of_range_mask = ~df[coord_col].between(
            *valid_range) & df[coord_col].notna()
        if out_of_range_mask.any():
            print(
                f"Warning: {out_of_range_mask.sum()} value(s) in '{coord_col}' out of valid range {valid_range}.")

# --- Flag rows with any issue for review ---
issue_mask = (
    df[text_cols].apply(lambda col: col.str.contains(
        r'[^\x20-\x7E]', regex=True, na=False)).any(axis=1)
    if all(c in df.columns for c in text_cols) else pd.Series(False, index=df.index)
)
coord_issue_mask = df[['latitude', 'longitude']].isna().any(axis=1) if {
    'latitude', 'longitude'}.issubset(df.columns) else pd.Series(False, index=df.index)

df_flagged = df[issue_mask | coord_issue_mask]
if len(df_flagged):
    df_flagged.to_csv(
        "Data/intermediate/settlement_masterlist_flagged_rows.csv", index=False)
    print(f"Saved {len(df_flagged)} flagged row(s) for review.")

# Save cleaned file
df.to_csv("Data/processed/settlement_masterlist_cleaned.csv", index=False)
print("Cleaned settlement masterlist saved.")


# Cleaning Daily tally sheet data

# Load data
tally_path = Path("Data/raw/etally_daily.csv")
masterlist_path = Path("Data/processed/settlement_masterlist_cleaned.csv")

df = pd.read_csv(tally_path)
masterlist = pd.read_csv(masterlist_path)

# Standardize campaign_date to real date values ---
n_before_parse = df['campaign_date'].notna().sum()
df['campaign_date'] = pd.to_datetime(
    df['campaign_date'], errors='coerce').dt.date

n_unparsed = df['campaign_date'].isna().sum()
if n_unparsed:
    print(
        f"Warning: {n_unparsed} row(s) had unparseable 'campaign_date' values.")

# --- Proper case for lga_name ---
if 'lga_name' in df.columns:
    df['lga_name'] = (
        df['lga_name']
        .astype(str)
        .str.strip()
        .str.replace(r'\s+', ' ', regex=True)
        .str.title()
    )

# --- Validate settlement_id against masterlist ---
valid_ids = set(masterlist['settlement_id'].astype(
    str).str.strip().str.upper())
df['settlement_id'] = df['settlement_id'].astype(str).str.strip().str.upper()

n_before = len(df)
id_mask = df['settlement_id'].isin(valid_ids)

# Save unmatched rows for review before dropping
df_unmatched = df[~id_mask].copy()
if len(df_unmatched):
    unmatched_path = Path(
        "Data/intermediate/tally_daily_unmatched_settlement_id.csv")
    df_unmatched.to_csv(unmatched_path, index=False)
    print(
        f"Warning: {len(df_unmatched)} row(s) with settlement_id not found in masterlist. Saved to {unmatched_path}")

# Keep only matched rows
df = df[id_mask].copy()

n_after = len(df)
print(f"Rows before settlement_id check: {n_before}")
print(
    f"Rows after settlement_id check:  {n_after} ({n_before - n_after} dropped)")

# Save cleaned tally
output_path = Path("Data/processed/etally_daily_cleaned.csv")
df.to_csv(output_path, index=False)
print(f"Cleaned tally saved to {output_path}")
