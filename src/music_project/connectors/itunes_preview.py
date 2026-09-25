"""
itunes_preview.py

Fetches 30-second Apple Music preview clips via the free, keyless
iTunes Search API (https://itunes.apple.com/search). No auth needed.

Usage:
    from music_project.connectors.itunes_preview import get_apple_preview, save_preview

    record = get_apple_preview("Đánh Đổi", "Obito, Shiki, RPT MCK", country="vn")
    if record:
        save_preview(record["previewUrl"], "danh_doi.m4a")
"""

import difflib
import os
import subprocess
import time

import requests

SEARCH_URL = "https://itunes.apple.com/search"


def _normalize(s):
    return s.lower().strip()


def _match_score(query_track, query_artist, candidate):
    """0-1 similarity between what you asked for and a candidate result,
    weighted toward the title since artist strings vary a lot (features,
    romanization, ordering)."""
    track_score = difflib.SequenceMatcher(
        None, _normalize(query_track), _normalize(candidate.get("trackName", ""))
    ).ratio()
    artist_score = difflib.SequenceMatcher(
        None, _normalize(query_artist), _normalize(candidate.get("artistName", ""))
    ).ratio()
    return 0.7 * track_score + 0.3 * artist_score


def _search(track_name, artist_name, country, limit):
    """
    Raw iTunes Search call. Only the first of multiple collaborators
    ("Obito, Shiki, RPT MCK") goes into the query - cramming every artist
    into the term turns it into a generic keyword search that can match an
    unrelated track sharing some of those names as features.
    """
    params = {
        "term": f"{track_name} {artist_name.split(',')[0].strip()}",
        "media": "music",
        "entity": "song",
        "limit": limit,
        "country": country,
    }
    resp = requests.get(SEARCH_URL, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json().get("results", [])


def get_apple_preview(track_name, artist_name, country="us", limit=5, min_score=0.5):
    """
    Query the iTunes Search API for one track.

    country: Apple's catalog is split per storefront - a track can be
    indexed on the Vietnamese store (country="vn") and simply absent from
    the US one. Pass the track's actual release market, not always "us".

    Pulls `limit` candidates and picks the one that actually matches by
    text similarity, instead of trusting the API's top result blindly.
    Returns None (with a printed warning) if nothing clears min_score,
    rather than silently returning a wrong track.
    """
    results = _search(track_name, artist_name, country, limit)
    if not results:
        return None

    best = max(results, key=lambda r: _match_score(track_name, artist_name, r))
    best_score = _match_score(track_name, artist_name, best)

    if best_score < min_score:
        print(
            f"Low-confidence match ({best_score:.2f}) for '{track_name}' - "
            f"{artist_name}: got '{best.get('trackName')}' - "
            f"{best.get('artistName')}. Try a different country storefront."
        )
        return None
    return best


def debug_search(track_name, artist_name, country="vn", limit=10):
    """
    Prints every candidate the API returns with its match score, instead
    of just the best (possibly rejected) one - useful when the top match
    is below min_score and you want to see what's actually available
    before concluding the track isn't on this storefront.
    """
    results = _search(track_name, artist_name, country, limit)
    if not results:
        print(f"No results at all for '{track_name}' on country='{country}'")
        return
    for r in sorted(results, key=lambda r: _match_score(track_name, artist_name, r), reverse=True):
        score = _match_score(track_name, artist_name, r)
        print(f"{score:.2f}  {r.get('trackName')} - {r.get('artistName')}")


def save_preview(preview_url, out_path="preview.m4a"):
    """Saves the raw preview clip exactly as Apple sends it (AAC in .m4a, no re-encoding)."""
    audio_bytes = requests.get(preview_url, timeout=10).content
    with open(out_path, "wb") as f:
        f.write(audio_bytes)
    return out_path


def save_and_play_preview(preview_url, out_path="preview.m4a"):
    """
    Saves the clip and plays it - the quickest way to confirm a match is
    actually the right song before it goes anywhere near the embedding
    pipeline.

    macOS only: uses `afplay`, which ships with every Mac.
    Linux: swap for subprocess.run(["ffplay", "-nodisp", "-autoexit", out_path]).
    """
    save_preview(preview_url, out_path)
    print(f"Saved to {os.path.abspath(out_path)}")
    subprocess.run(["afplay", out_path])
    return out_path


def enrich_library_with_previews(df, name_col="Name", artist_col="Artist", country="us", sleep=0.2):
    """
    Adds a `preview_url` column to a track DataFrame by looking each track up.
    `country`: a fixed "us" will silently miss/mismatch non-US releases.
    `sleep` throttles requests - there's no published rate limit for this
    public endpoint, but it's polite (and safer) not to hammer it.
    """
    previews = []
    for _, row in df.iterrows():
        rec = get_apple_preview(row[name_col], row[artist_col], country=country)
        previews.append(rec["previewUrl"] if rec else None)
        time.sleep(sleep)
    df["preview_url"] = previews
    return df
