from datasets import load_dataset
import pandas as pd
from huggingface_hub import HfFileSystem

fs = HfFileSystem()
repo = "datasets/CongtyTuban/tessst"

df_2025 = pd.read_json(f"hf://{repo}/Streaming_History_Audio_2025.json")

files = fs.glob(f"{repo}/Streaming_History_Audio_*.json")
df_audio = pd.concat([pd.read_json(f"hf://{f}") for f in files], ignore_index=True)

print(df_audio.head())