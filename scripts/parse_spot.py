import json
import pandas as pd



path = "data/raw_spot/Spotify_bhuy/Streaming_History_Audio_2024.json"
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)

df = pd.DataFrame(data)

print(df.shape)
print(df.info())
print(df.head())