"""Offline check for apple_insights on a tiny synthetic library. Run: uv run python tests/test_apple_insights.py"""

from datetime import datetime

from music_project.analysis.apple_insights import (
    build_profile, enrich_tracks, script_lang, split_artists, version_tags, AppleLibrary,
)
from music_project.connectors.apple_music import playlists_to_df, tracks_to_df
import pandas as pd

SNAP = datetime(2026, 8, 1)


def _track(tid, name, artist, genre, plays=0, skips=0, loved=False, added=datetime(2026, 5, 1),
           released=datetime(2026, 4, 20), played=None):
    t = {"Track ID": tid, "Name": name, "Artist": artist, "Genre": genre, "Total Time": 200_000,
         "Year": released.year, "Date Added": added, "Release Date": released, "Track Type": "Remote"}
    if plays:
        t["Play Count"] = plays
        t["Play Date UTC"] = played or datetime(2026, 7, 30)
    if skips:
        t["Skip Count"] = skips
    if loved:
        t["Loved"] = True
    return t


def demo():
    # Parsing helpers
    assert split_artists("Obito & Shiki", "Xa Xôi (feat. RPT MCK)") == ["Obito", "Shiki", "RPT MCK"]
    assert split_artists("Lil Nas X", None) == ["Lil Nas X"]
    assert version_tags("Song (Slowed + Reverb)") == ["slowed", "reverb"]
    assert version_tags("Live Forever") == []  # "live" only counts in decorations
    assert script_lang("Xin Lỗi") == "vi" and script_lang("Đen") == "vi"
    assert script_lang("사랑") == "ko" and script_lang("桃花诺") == "zh" and script_lang("Hello") == "latin"

    tracks = {
        "1": _track(1, "Hit (feat. B)", "A", "Pop", plays=50, loved=True),
        "2": _track(2, "Meh", "A", "Pop", plays=1, skips=6),
        "3": _track(3, "Old Fave", "C", "Rock", plays=60, played=datetime(2025, 6, 1),
                    released=datetime(1999, 1, 1)),
        "4": _track(4, "Never", "D", "Jazz"),
    }
    raw = {
        "Date": SNAP,
        "Tracks": tracks,
        "Playlists": [
            {"Name": "Library", "Master": True, "Playlist ID": 1, "Playlist Items": [{"Track ID": i} for i in (1, 2, 3, 4)]},
            {"Name": "Rainy", "Playlist ID": 2, "Playlist Items": [{"Track ID": 1}, {"Track ID": 3}]},
        ],
    }
    snap = pd.Timestamp(SNAP, tz="UTC")
    playlists = playlists_to_df(raw)
    t = enrich_tracks(tracks_to_df(raw, "u"), playlists, snap).set_index("track_id")
    lib = AppleLibrary("u", t.reset_index(), playlists, snap)

    # Features
    assert list(playlists["is_user_curated"].unique()) == [False, True]
    assert t.loc[1, "artists"] == ["A", "B"] and t.loc[1, "playlist_names"] == ["Rainy"]
    assert t.loc[1, "affinity"] > t.loc[3, "affinity"] > t.loc[4, "affinity"] == 0 > t.loc[2, "affinity"]
    assert t.loc[2, "likely_dislike"] and not t.loc[1, "likely_dislike"]
    assert t.loc[1, "freshness"] == "new (<30d)" and t.loc[3, "freshness"] == "deep catalog (>5y)"

    # Profile
    p = build_profile(lib)
    assert p["summary"]["tracks"] == 4 and p["summary"]["loved"] == 1
    assert p["genres"].index[0] == "Pop"
    assert "Old Fave" in set(p["segments"]["forgotten_favorites"]["name"])   # heavy, unplayed >180d
    assert list(p["segments"]["unplayed_saves"]["name"]) == ["Never"]
    assert list(p["segments"]["likely_dislikes"]["name"]) == ["Meh"]
    assert p["playlists"].loc[0, "playlist"] == "Rainy" and not p["playlists"].loc[0, "bulk_dump"]
    assert p["artist_cooccurrence"].iloc[0][["artist_a", "artist_b"]].tolist() == ["A", "C"]
    print("ok")


if __name__ == "__main__":
    demo()
