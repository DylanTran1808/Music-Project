"""
Download 30-second Apple Music preview clips for every song listed in a
CSV (Name, Artist columns), using the free, keyless iTunes Search API.

Songs with no confident match are logged and skipped - the script keeps
going instead of stopping on the first miss.

Install dependencies first:
    pip install pandas requests

Usage:
    python download_previews.py
"""

import difflib
import logging
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests

# ----------------------------- CONFIG -----------------------------
INPUT_CSV = "data/temp_vi_en_songs.csv"        # CSV with a song-name and artist column
NAME_COLUMN = "Name"
ARTIST_COLUMN = "Artist"
OUTPUT_DIR = "previews"              # folder previews get saved into
COUNTRIES = ["us", "vn"]             # storefronts to try, in order, per song
MIN_SCORE = 0.5                      # minimum match confidence to accept a result
SLEEP_BETWEEN_REQUESTS = 0.5         # be polite to the (free, keyless) API
# --------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("previews")


def _normalize(s):
    return s.lower().strip()


def _match_score(query_track, query_artist, candidate):
    """0-1 similarity between what was asked for and a candidate result,
    weighted toward the title since artist strings vary a lot (features,
    romanization, ordering)."""
    track_score = difflib.SequenceMatcher(
        None, _normalize(query_track), _normalize(candidate.get("trackName", ""))
    ).ratio()
    artist_score = difflib.SequenceMatcher(
        None, _normalize(query_artist), _normalize(candidate.get("artistName", ""))
    ).ratio()
    return 0.7 * track_score + 0.3 * artist_score


def search_track(track_name, artist_name, country, limit=5):
    primary_artist = artist_name.split(",")[0].strip()
    params = {
        "term": f"{track_name} {primary_artist}",
        "media": "music",
        "entity": "song",
        "limit": limit,
        "country": country,
    }
    resp = requests.get("https://itunes.apple.com/search", params=params, timeout=10)
    resp.raise_for_status()
    return resp.json().get("results", [])


def find_best_match(track_name, artist_name, countries, min_score):
    """Try each storefront in order (a track can be indexed on one
    country's catalog and absent from another), return the first
    confident match found."""
    for country in countries:
        results = search_track(track_name, artist_name, country)
        if not results:
            continue
        best = max(results, key=lambda r: _match_score(track_name, artist_name, r))
        score = _match_score(track_name, artist_name, best)
        if score >= min_score:
            return best, score, country
    return None, 0.0, None


def sanitize_filename(name):
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    return name.strip()[:150]  # keep filenames from getting absurdly long


def download_preview(preview_url, out_path):
    audio_bytes = requests.get(preview_url, timeout=15).content
    with open(out_path, "wb") as f:
        f.write(audio_bytes)


def main():
    df = pd.read_csv(INPUT_CSV)
    for col in (NAME_COLUMN, ARTIST_COLUMN):
        if col not in df.columns:
            sys.exit(f"Column '{col}' not found in {INPUT_CSV}. Available columns: {list(df.columns)}")

    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    total = len(df)
    downloaded = 0
    skipped = 0

    for i, row in df.iterrows():
        track_name = str(row[NAME_COLUMN]).strip()
        artist_name = str(row[ARTIST_COLUMN]).strip()
        progress = f"[{i + 1}/{total}]"

        if not track_name or track_name.lower() == "nan":
            log.info(f"{progress} empty song name - skipping")
            skipped += 1
            continue

        log.info(f"{progress} searching: '{track_name}' - {artist_name}")

        try:
            best, score, country = find_best_match(track_name, artist_name, COUNTRIES, MIN_SCORE)
        except requests.RequestException as e:
            log.info(f"{progress} search request failed ({e}) - skipping")
            skipped += 1
            time.sleep(SLEEP_BETWEEN_REQUESTS)
            continue

        if not best:
            log.info(f"{progress} no confident match found - skipping")
            skipped += 1
            time.sleep(SLEEP_BETWEEN_REQUESTS)
            continue

        preview_url = best.get("previewUrl")
        if not preview_url:
            log.info(f"{progress} match found but no preview available - skipping")
            skipped += 1
            time.sleep(SLEEP_BETWEEN_REQUESTS)
            continue

        filename = sanitize_filename(f"{track_name} - {artist_name}") + ".m4a"
        out_path = Path(OUTPUT_DIR) / filename

        if out_path.exists():
            log.info(f"{progress} already downloaded - skipping")
            time.sleep(SLEEP_BETWEEN_REQUESTS)
            continue

        try:
            download_preview(preview_url, out_path)
            downloaded += 1
            log.info(
                f"{progress} matched '{best.get('trackName')}' - {best.get('artistName')} "
                f"(score {score:.2f}, country={country}) -> saved {out_path.name}"
            )
        except Exception as e:
            log.info(f"{progress} download failed ({e}) - skipping")
            skipped += 1

        time.sleep(SLEEP_BETWEEN_REQUESTS)

    log.info(f"Done. Downloaded {downloaded}/{total}, skipped {skipped}/{total}.")


if __name__ == "__main__":
    main()