"""
spotify.py

Spotify connector for the Streaming-data HF dataset repo.
Reads streaming-history JSON exports from raw_spot/Spotify_<username>/*.json.
"""

import json
from typing import Iterator, Optional

import pandas as pd

from .common import DEFAULT_REPO_ID, HF_TOKEN, RAW_SPOT_DIR, all_repo_files, get_fs, hf_path


# ---------------------------------------------------------------------------
# Repo browsing
# ---------------------------------------------------------------------------

def list_spotify_users(repo_id: str = DEFAULT_REPO_ID, token: Optional[str] = HF_TOKEN) -> list:
    """List Spotify usernames, derived from the raw_spot/Spotify_<user>/ folder names."""
    files = all_repo_files(repo_id, token)
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
    files = all_repo_files(repo_id, token)
    folder = f"{RAW_SPOT_DIR}/Spotify_{username}/"
    return [f for f in files if f.startswith(folder) and f.endswith(".json")]


# ---------------------------------------------------------------------------
# Streaming / loading
# ---------------------------------------------------------------------------

def stream_spotify_json(path_in_repo: str, repo_id: str = DEFAULT_REPO_ID, token: Optional[str] = HF_TOKEN) -> Iterator[dict]:
    """
    Streams one Spotify JSON export record-by-record instead of loading the
    whole file into memory. Handles both a top-level JSON array and JSON Lines.

    Usage:
        for record in stream_spotify_json("raw_spot/Spotify_alex/StreamingHistory0.json"):
            handle(record)
    """
    fs = get_fs(token)
    path = hf_path(repo_id, path_in_repo)

    with fs.open(path, "r", encoding="utf-8") as f:
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
    df = pd.DataFrame(records)
    if not df.empty:
        df["source_user"] = username
    return df