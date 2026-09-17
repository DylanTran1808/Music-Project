from music_project.connectors.HuggingFaceConnector import *


df = apple_music_library_to_df("raw_am/Library_kien.xml")

print(df.head())