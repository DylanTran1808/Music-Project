"""
hf_music_connector.py

Connects to the "Streaming-data" Hugging Face dataset repo for Project-Music
and streams data on demand instead of downloading everything up front.

Repo layout expected:
    raw_am/                      -> Apple Music library export(s), .xml (plist format)
    raw_spot/
        Spotify_<username>/      -> one folder per Spotify user
            *.json                -> that user's Spotify streaming-history exports

Do 'uv sync' before use

Auth (only needed for a private repo):
    set the HF_TOKEN environment variable --> please do this
    
"""

import os
import json
import plistlib
from typing import Iterator, Optional

import pandas as pd
from huggingface_hub import HfFileSystem, hf_hub_download, list_repo_files
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

load_dotenv()

DEFAULT_REPO_ID = "CongtyTuban/Streaming-data"  # adjust if the namespace differs
HF_TOKEN = os.getenv("HF_TOKEN")            

RAW_AM_DIR = "raw_am"
RAW_SPOT_DIR = "raw_spot"


# ---------------------------------------------------------------------------
# Repo browsing helpers
# ---------------------------------------------------------------------------

def _all_repo_files(repo_id: str = DEFAULT_REPO_ID, token: Optional[str] = HF_TOKEN) -> list:
    return list_repo_files(repo_id, repo_type="dataset", token=token)


def list_apple_music_files(repo_id: str = DEFAULT_REPO_ID, token: Optional[str] = HF_TOKEN) -> list:
    """List every .xml file under raw_am/."""
    files = _all_repo_files(repo_id, token)
    return [f for f in files if f.startswith(f"{RAW_AM_DIR}/") and f.endswith(".xml")]


def list_spotify_users(repo_id: str = DEFAULT_REPO_ID, token: Optional[str] = HF_TOKEN) -> list:
    """List Spotify usernames, derived from the raw_spot/Spotify_<user>/ folder names."""
    files = _all_repo_files(repo_id, token)
    users = set()
    prefix = f"{RAW_SPOT_DIR}/Spotify_"
    for f in files:
        if f.startswith(prefix):
            remainder = f[len(prefix):]
            username = remainder.split("/", 1)[0]
            if username:
                users.add(username)
    return sorted(users)


def list_spotify_files(username: str, repo_id: str = DEFAULT_REPO_ID, token: Optional[str] = HF_TOKEN) -> list:
    """List every .json file for a given Spotify user, e.g. list_spotify_files('alex')."""
    files = _all_repo_files(repo_id, token)
    folder = f"{RAW_SPOT_DIR}/Spotify_{username}/"
    return [f for f in files if f.startswith(folder) and f.endswith(".json")]


# ---------------------------------------------------------------------------
# Apple Music (.xml / plist) — raw_am
# ---------------------------------------------------------------------------

def load_apple_music_library(username: str, repo_id: str = DEFAULT_REPO_ID, token: Optional[str] = HF_TOKEN) -> dict:
    """
    Loads a full Apple Music XML library export (plist format) into a dict.
    Use this when you need the whole library at once, e.g. to build a DataFrame.

    Usage:
        lib = load_apple_music_library(username = "kien")
        tracks = lib["Tracks"]  # dict keyed by track ID
    """
    fs = HfFileSystem(token=token)
    hf_path = f"hf://datasets/{repo_id}/{RAW_AM_DIR}/Library_{username}.xml"
    with fs.open(hf_path, "rb") as f:
        return plistlib.load(f)


def stream_apple_music_tracks(username: str, repo_id: str = DEFAULT_REPO_ID, token: Optional[str] = HF_TOKEN) -> Iterator[dict]:
    """
    Yields one track dict at a time from an Apple Music XML export, so you
    don't have to hold every track in memory if you're just scanning/filtering.
    (The plist is parsed once, but tracks are handed out lazily via a
    generator so downstream code can process-and-discard row by row.)

    Usage:
        for track in stream_apple_music_tracks(username = "kien"):
            process(track)
    """
    lib = load_apple_music_library(username, repo_id, token)
    for track_id, track in lib.get("Tracks", {}).items():
        track["Track ID"] = track.get("Track ID", track_id)
        yield track


def apple_music_library_to_df(username: str, repo_id: str = DEFAULT_REPO_ID, token: Optional[str] = HF_TOKEN) -> pd.DataFrame:
    """Convenience: load an Apple Music XML export straight into a DataFrame of tracks."""
    lib = load_apple_music_library(username, repo_id, token)
    return pd.DataFrame(list(lib.get("Tracks", {}).values()))


# ---------------------------------------------------------------------------
# Spotify (.json) — raw_spot/Spotify_<user>/
# ---------------------------------------------------------------------------
def stream_spotify_json(path_in_repo: str, repo_id: str = DEFAULT_REPO_ID, token: Optional[str] = HF_TOKEN) -> Iterator[dict]:
    """
    Streams one Spotify JSON export record-by-record instead of loading the
    whole file into memory. Handles both a top-level JSON array and JSON Lines.

    Usage:
        for record in stream_spotify_json("raw_spot/Spotify_alex/StreamingHistory0.json"):
            handle(record)
    """
    fs = HfFileSystem(token=token)
    hf_path = f"hf://datasets/{repo_id}/{path_in_repo}"

    with fs.open(hf_path, "r", encoding="utf-8") as f:
        first_char = f.read(1)
        f.seek(0)

        if first_char == "[":
            data = json.load(f)
            for record in data:
                yield record
        else:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)


def stream_all_spotify_for_user(username: str, repo_id: str = DEFAULT_REPO_ID, token: Optional[str] = HF_TOKEN) -> Iterator[dict]:
    """Streams every record across all of one user's Spotify JSON files, in turn."""
    for path in list_spotify_files(username, repo_id, token):
        yield from stream_spotify_json(path, repo_id, token)

_APPLE_MUSIC_COLUMN_MAP = {
    "Track ID": "track_id",
    "Name": "name",
    "Kind": "kind",
    "Size": "size_bytes",
    "Total Time": "total_time_ms",
    "Date Modified": "date_modified",
    "Date Added": "date_added",
    "Bit Rate": "bit_rate",
    "Sample Rate": "sample_rate",
    "Play Count": "play_count",
    "Play Date": "play_date",
    "Play Date UTC": "play_date_utc",
    "Skip Count": "skip_count",
    "Skip Date": "skip_date",
    "Normalization": "normalization",
    "Persistent ID": "persistent_id",
    "Track Type": "track_type",
    "Location": "location",
    "File Folder Count": "file_folder_count",
    "Library Folder Count": "library_folder_count",
    "Artist": "artist",
    "Album Artist": "album_artist",
    "Composer": "composer",
    "Album": "album",
    "Genre": "genre",
    "Disc Number": "disc_number",
    "Disc Count": "disc_count",
    "Track Number": "track_number",
    "Track Count": "track_count",
    "Year": "year",
    "Release Date": "release_date",
    "Artwork Count": "artwork_count",
    "Sort Album": "sort_album",
    "Sort Artist": "sort_artist",
    "Sort Name": "sort_name",
    "Explicit": "explicit",
    "Apple Music": "apple_music",
    "Favorited": "favorited",
    "Loved": "loved",
    "Protected": "protected",
    "Purchased": "purchased",
    "Compilation": "compilation",
    "Playlist Only": "playlist_only",
    "Sort Album Artist": "sort_album_artist",
    "Work": "work",
    "Movement Name": "movement_name",
    "Grouping": "grouping",
    "Movement Number": "movement_number",
    "Movement Count": "movement_count",
    "Part Of Gapless Album": "part_of_gapless_album",
    "Rating": "rating",
    "Album Rating": "album_rating",
    "Album Rating Computed": "album_rating_computed",
    "Sort Composer": "sort_composer",
    "Clean": "clean",
}

_APPLE_MUSIC_DATETIME_COLS = ["date_modified", "date_added", "play_date_utc", "skip_date", "release_date"]
_APPLE_MUSIC_BOOL_COLS = [
    "explicit", "apple_music", "favorited", "loved", "protected",
    "purchased", "compilation", "playlist_only", "part_of_gapless_album", "clean",
]
 
 
def apple_music_library_to_df(
    username: str,
    repo_id: str = DEFAULT_REPO_ID,
    token: Optional[str] = HF_TOKEN,
    coerce_types: bool = True,
) -> pd.DataFrame:
    """
    Load an Apple Music XML export straight into a DataFrame of tracks, with
    columns renamed to snake_case for easy attribute-style / query access
    (e.g. df.play_count, df.query("genre == 'Rock'")).
 
    Set coerce_types=False to get the raw values plistlib returns (no dtype
    conversion), e.g. if you want to inspect the export before cleaning it.
    """
    lib = load_apple_music_library(username, repo_id, token)
    df = pd.DataFrame(list(lib.get("Tracks", {}).values()))
 
    df = df.rename(columns=_APPLE_MUSIC_COLUMN_MAP)
    # Any column plistlib produced that isn't in the map keeps its original
    # name (rather than being silently dropped), so nothing is lost.
 
    if coerce_types and not df.empty:
        for col in _APPLE_MUSIC_DATETIME_COLS:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
 
        for col in _APPLE_MUSIC_BOOL_COLS:
            if col in df.columns:
                df[col] = df[col].fillna(False).astype(bool)
 
        if "total_time_ms" in df.columns:
            df["total_time_ms"] = pd.to_numeric(df["total_time_ms"], errors="coerce")
            df["duration_sec"] = df["total_time_ms"] / 1000
 
        for col in ["play_count", "skip_count", "year", "bit_rate", "sample_rate", "rating", "album_rating"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
 
    df["source_user"] = username
    return df


# ---------------------------------------------------------------------------
# Download-to-disk (when you need a local file instead of streaming)
# ---------------------------------------------------------------------------

def download_file(path_in_repo: str, repo_id: str = DEFAULT_REPO_ID, token: Optional[str] = HF_TOKEN) -> str:
    """Downloads (and caches locally) one file from the repo, returning the local path."""
    return hf_hub_download(repo_id=repo_id, filename=path_in_repo, repo_type="dataset", token=token)


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Apple Music files (raw_am/):")
    for f in list_apple_music_files():
        print(" -", f)

    print("\nSpotify users (raw_spot/):")
    users = list_spotify_users()
    for u in users:
        print(" -", u)

    if users:
        first_user = users[0]
        print(f"\nFiles for {first_user}:")
        for f in list_spotify_files(first_user):
            print(" -", f)