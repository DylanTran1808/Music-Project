import plistlib

import pandas as pd


path = "data/raw_am/Library_cuong.xml"

with open(path, "rb") as f:
    data = plistlib.load(f)

tracks = data["Tracks"]
print(data)
df = pd.DataFrame.from_dict(tracks, orient="index")

print(df.shape)
print(df.columns.tolist())  