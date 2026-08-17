import json
import pandas as pd



path = "rawdata_spotify/Spotify_bhuy/Streaming_History_Video_2025.json"
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)

df = pd.DataFrame(data)

print(df.shape)
print(df.info())
print(df.head())