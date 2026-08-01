import glob
import pandas as pd
file_path = glob.glob("Data/raw/tracks/*.csv")
df_list = []
for file in file_path:
    df = pd.read_csv(file)
    df_list.append(df)
master_df = pd.concat(df_list, ignore_index=True)
master_df.to_csv("Data/processed/master_tracks.csv", index=False)
