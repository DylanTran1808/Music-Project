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


def spotify_user_to_df(username: str, repo_id: str = DEFAULT_REPO_ID, token: Optional[str] = HF_TOKEN) -> pd.DataFrame:
    """Convenience: load all of one user's Spotify streaming history into a single DataFrame."""
    records = list(stream_all_spotify_for_user(username, repo_id, token))
    return pd.DataFrame(records)


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