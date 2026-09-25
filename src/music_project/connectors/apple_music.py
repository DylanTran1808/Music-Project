"""
apple_music.py

Apple Music connector for the Streaming-data HF dataset repo.
Reads XML library exports (plist format) from raw_am/Library_<username>.xml.
"""

import plistlib
from typing import Iterator, Optional

import pandas as pd

from .common import DEFAULT_REPO_ID, HF_TOKEN, RAW_AM_DIR, all_repo_files, get_fs, hf_path


# ---------------------------------------------------------------------------
# Repo browsing
# ---------------------------------------------------------------------------

def list_apple_music_files(repo_id: str = DEFAULT_REPO_ID, token: Optional[str] = HF_TOKEN) -> list:
    """List every .xml file under raw_am/."""
    files = all_repo_files(repo_id, token)
    return [f for f in files if f.startswith(f"{RAW_AM_DIR}/") and f.endswith(".xml")]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_apple_music_library(username: str, repo_id: str = DEFAULT_REPO_ID, token: Optional[str] = HF_TOKEN) -> dict:
    """
    Loads a full Apple Music XML library export (plist format) into a dict.
    Use this when you need the whole library at once, e.g. to build a DataFrame.

    Usage:
        lib = load_apple_music_library(username="kien")
        tracks = lib["Tracks"]  # dict keyed by track ID
    """
    fs = get_fs(token)
    path = hf_path(repo_id, RAW_AM_DIR, f"Library_{username}.xml")
    with fs.open(path, "rb") as f:
        return plistlib.load(f)


def stream_apple_music_tracks(username: str, repo_id: str = DEFAULT_REPO_ID, token: Optional[str] = HF_TOKEN) -> Iterator[dict]:
    """
    Yields one track dict at a time from an Apple Music XML export, so you
    don't have to hold every track in memory if you're just scanning/filtering.
    (The plist is parsed once, but tracks are handed out lazily via a
    generator so downstream code can process-and-discard row by row.)

    Usage:
        for track in stream_apple_music_tracks(username="kien"):
            process(track)
    """
    lib = load_apple_music_library(username, repo_id, token)
    for track_id, track in lib.get("Tracks", {}).items():
        track["Track ID"] = track.get("Track ID", track_id)
        yield track


# ---------------------------------------------------------------------------
# Column renaming + type coercion
# ---------------------------------------------------------------------------

# Explicit rename map (rather than an automatic snake_case pass) so the
# result is predictable and easy to tab-complete/autocomplete in pandas.
_COLUMN_MAP = {
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

_DATETIME_COLS = ["date_modified", "date_added", "play_date_utc", "skip_date", "release_date"]
_BOOL_COLS = [
    "explicit", "apple_music", "favorited", "loved", "protected",
    "purchased", "compilation", "playlist_only", "part_of_gapless_album", "clean",
]
_INT_COLS = ["play_count", "skip_count", "year", "bit_rate", "sample_rate", "rating", "album_rating"]


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
    return tracks_to_df(lib, username, coerce_types)


def tracks_to_df(lib: dict, username: str, coerce_types: bool = True) -> pd.DataFrame:
    """Same as apple_music_library_to_df, but from an already-loaded library dict."""
    df = pd.DataFrame(list(lib.get("Tracks", {}).values()))

    df = df.rename(columns=_COLUMN_MAP)
    # Any column plistlib produced that isn't in the map keeps its original
    # name (rather than being silently dropped), so nothing is lost.

    if coerce_types and not df.empty:
        for col in _DATETIME_COLS:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)

        for col in _BOOL_COLS:
            if col in df.columns:
                df[col] = df[col].fillna(False).astype(bool)

        if "total_time_ms" in df.columns:
            df["total_time_ms"] = pd.to_numeric(df["total_time_ms"], errors="coerce")
            df["duration_sec"] = df["total_time_ms"] / 1000

        for col in _INT_COLS:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

    df["source_user"] = username
    return df

# ---------------------------------------------------------------------------
# Playlists
# ---------------------------------------------------------------------------

# Keys Apple sets on built-in playlists (Library, Music, Downloaded, Movies...).
_SYSTEM_PLAYLIST_KEYS = ("Master", "Distinguished Kind", "Music", "Movies", "TV Shows", "Podcasts", "Audiobooks")


def playlists_to_df(lib: dict) -> pd.DataFrame:
    """
    Long-form playlist membership: one row per (playlist, track), in playlist order.

    is_user_curated is False for built-in, folder, and smart (rule-based)
    playlists - only hand-built playlists say something about taste.
    """
    rows = []
    for p in lib.get("Playlists", []):
        curated = not (any(k in p for k in _SYSTEM_PLAYLIST_KEYS) or p.get("Folder") or "Smart Info" in p)
        for pos, item in enumerate(p.get("Playlist Items", [])):
            rows.append({
                "playlist_id": p.get("Playlist ID"),
                "playlist_persistent_id": p.get("Playlist Persistent ID"),
                "playlist_name": p.get("Name"),
                "description": p.get("Description", ""),
                "is_user_curated": curated,
                "position": pos,
                "track_id": item["Track ID"],
            })
    cols = ["playlist_id", "playlist_persistent_id", "playlist_name", "description",
            "is_user_curated", "position", "track_id"]
    return pd.DataFrame(rows, columns=cols)
