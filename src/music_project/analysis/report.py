"""
report.py

One report per user across both sources: the Apple Music profile
(apple_insights), the Spotify profile (spotify_insights), and how the two
overlap when a user has both.

Usage:
    from music_project.analysis.report import build_report

    report = build_report(username="kien")
    if report["apple"]:
        print(report["apple"]["artists"])
    if report["spotify"]:
        print(report["spotify"]["artists"])
    if report["overlap"]:
        print(report["overlap"]["shared_artists"])

Or run as a script:
    uv run python -m music_project.analysis.report --user kien
"""

import argparse
from typing import Optional

import pandas as pd

from music_project.analysis import apple_insights, spotify_insights
from music_project.analysis.apple_insights import print_profile
from music_project.connectors.apple_music import list_apple_music_files
from music_project.connectors.spotify import list_spotify_users


# ---------------------------------------------------------------------------
# Availability checks
# ---------------------------------------------------------------------------

def has_apple_music_data(username: str) -> bool:
    """Whether raw_am/Library_<username>.xml exists in the repo."""
    target = f"raw_am/Library_{username}.xml"
    return target in list_apple_music_files()


def has_spotify_data(username: str) -> bool:
    """Whether a raw_spot/Spotify_<username>/ folder exists in the repo."""
    return username in list_spotify_users()


# ---------------------------------------------------------------------------
# Cross-source: Apple Music library vs Spotify streaming history
# ---------------------------------------------------------------------------

def source_overlap(apple_tracks: pd.DataFrame, spotify_tracks: pd.DataFrame) -> dict:
    """
    Artists and tracks present in both sources. Tracks match on track_key
    (primary artist + base title), which both insight modules build the same way.
    """
    def artists(t):
        return set(t["artists"].explode().dropna().str.casefold())

    a_art, s_art = artists(apple_tracks), artists(spotify_tracks)
    a_trk, s_trk = set(apple_tracks["track_key"]), set(spotify_tracks["track_key"])
    jaccard = lambda a, b: round(len(a & b) / len(a | b), 3) if a | b else None
    return {
        "shared_artists": sorted(a_art & s_art),
        "only_in_apple_music": sorted(a_art - s_art),
        "only_in_spotify": sorted(s_art - a_art),
        "shared_artist_count": len(a_art & s_art),
        "artist_jaccard": jaccard(a_art, s_art),
        "shared_track_count": len(a_trk & s_trk),
        "track_jaccard": jaccard(a_trk, s_trk),
    }


# ---------------------------------------------------------------------------
# Full report
# ---------------------------------------------------------------------------

def build_report(username: str) -> dict:
    """
    Pulls whichever sources are actually available for this user. A source
    that isn't present comes back as None rather than raising — most people
    will only have one of the two platforms hooked up for a given username.
    """
    apple_available = has_apple_music_data(username)
    spotify_available = has_spotify_data(username)

    lib = apple_insights.load_library(username) if apple_available else None
    hist = spotify_insights.load_history(username) if spotify_available else None

    return {
        "apple_available": apple_available,
        "spotify_available": spotify_available,
        "apple": apple_insights.build_profile(lib) if lib else None,
        "spotify": spotify_insights.build_profile(hist) if hist else None,
        # Overlap only makes sense with both sources present.
        "overlap": source_overlap(lib.tracks, hist.tracks) if (lib and hist) else None,
    }


def print_report(report: dict, top_n: int = 10) -> None:
    """Pretty-prints build_report() to the console, skipping any missing source."""
    for key, title in [("apple", "APPLE MUSIC"), ("spotify", "SPOTIFY")]:
        if report[key]:
            print("=" * 60 + f"\n{title}\n" + "=" * 60)
            print_profile(report[key], top_n)
            print()
        else:
            print(f"No {title.title()} data found for this user — skipping.\n")

    if report["overlap"]:
        print("=" * 60 + "\nOVERLAP\n" + "=" * 60)
        for k in ["shared_artist_count", "artist_jaccard", "shared_track_count", "track_jaccard"]:
            print(f"  {k}: {report['overlap'][k]}")
    else:
        print("Only one platform's data is available for this user — no overlap to compute.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build a listening-insights report for one user.")
    parser.add_argument("--user", required=True, help="Username (matches Library_<user>.xml / Spotify_<user>/)")
    parser.add_argument("--top-n", type=int, default=10)
    args = parser.parse_args()

    print_report(build_report(username=args.user), args.top_n)
