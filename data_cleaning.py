from datetime import time
import numpy as np
import pandas as pd
df = pd.read_csv('Data/intermediate/master_tracks.csv')
df = df.dropna(subset=['accuracy_m', 'speed_kmh'], how='all')

# count missing values in accuracy_m column
missing_acc = df['accuracy_m'].isna().sum()
# 2️⃣ Fill missing spots with random numbers between 15 and 20
df.loc[df['accuracy_m'].isna(), 'accuracy_m'] = np.random.uniform(
    15, 20, size=missing_acc)

# count missing values in speed_kmh column
missing_speed = df['speed_kmh'].isna().sum()
# 2️⃣ Fill missing spots with random numbers between 1 and 5
df.loc[df['speed_kmh'].isna(), 'speed_kmh'] = np.random.uniform(
    1, 5, size=missing_speed)

# convert timestamp column to datetime format
df['timestamp'] = pd.to_datetime(df['timestamp'])

# set threshold for time difference (in seconds)
start_time = time(6, 0, 0)  # 6:00 AM
end_time = time(18, 0, 0)   # 6:00 PM
state_date = pd.to_datetime('2026-03-09').date()
end_date = pd.to_datetime('2026-03-13').date()

# create filters (true if it is inside the boundry, false if it is outside the boundry)
time_mask = (df['timestamp'].dt.time >= start_time) & (
    df['timestamp'].dt.time <= end_time)
date_mask = (df['timestamp'].dt.date >= state_date) & (
    df['timestamp'].dt.date <= end_date)
df = df[time_mask & date_mask]
df = df[(df['accuracy_m'] <= 50) & (df['speed_kmh'] <= 10)]

# save the cleaned data to a new CSV file
df.to_csv('Data/processed/cleaned_tracks.csv', index=False)
