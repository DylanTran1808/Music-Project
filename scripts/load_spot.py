"""
Load Spotify "Extended Streaming History" JSON exports into the multi-source
music database. Spotify splits your history into multiple files, often one
(or more) per year — pass them all at once with a glob.

Populates: artists, albums, tracks, spotify_tracks, listening_events,
and recomputes aggregate user_track_stats for this user/source from those events.

Usage:
    python load_spotify.py --json "data/spotify/alice/*.json" --user "Dylan"

    # or list files explicitly (shell-expanded globs also work):
    python load_spotify.py --json StreamingHistory_2023.json StreamingHistory_2024.json --user "Dylan"
"""

import argparse
import glob
import json
import math
import os

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# 1. Load + concatenate the year-split JSON files
# ---------------------------------------------------------------------------

def load_spotify_json(patterns) -> pd.DataFrame:
    """Accepts a list of file paths and/or glob patterns, reads every
    matching JSON file, and concatenates them into one DataFrame."""
    paths = []
    for pattern in patterns:
        matches = glob.glob(pattern)
        paths.extend(matches if matches else [pattern])

    paths = sorted(set(paths))
    if not paths:
        raise FileNotFoundError(f"No files matched: {patterns}")

    frames = []
    for path in paths:
        with open(path, "r", encoding="utf-8") as f:
            records = json.load(f)
        frames.append(pd.DataFrame(records))
        print(f"  loaded {len(records)} events from {os.path.basename(path)}")

    df = pd.concat(frames, ignore_index=True)
    return df


# ---------------------------------------------------------------------------
# 2. Cleaning helpers (same conventions as load_apple_music.py)
# ---------------------------------------------------------------------------

def clean(val):
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    return val


def to_bool(val):
    val = clean(val)
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("yes", "true", "1")
    return bool(val)


def to_int(val):
    val = clean(val)
    return int(val) if val is not None else None


def to_timestamp(val):
    val = clean(val)
    if val is None:
        return None
    try:
        return pd.to_datetime(val, utc=True).tz_localize(None)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 3. Get-or-create helpers, with an in-memory cache
#    (Spotify histories can be tens of thousands of rows, so we avoid a
#    round trip to Postgres for every single event.)
# ---------------------------------------------------------------------------

class Resolver:
    def __init__(self, cur):
        self.cur = cur
        self.artist_cache = {}          # name -> artist_id
        self.album_cache = {}           # (title, artist_id) -> album_id
        self.track_cache = {}           # (name, artist_id, album_id) -> track_id

    def get_or_create_artist(self, name):
        if not name:
            return None
        if name in self.artist_cache:
            return self.artist_cache[name]
        self.cur.execute("SELECT artist_id FROM artists WHERE name = %s", (name,))
        row = self.cur.fetchone()
        if row:
            artist_id = row[0]
        else:
            self.cur.execute(
                "INSERT INTO artists (name) VALUES (%s) RETURNING artist_id",
                (name,),
            )
            artist_id = self.cur.fetchone()[0]
        self.artist_cache[name] = artist_id
        return artist_id

    def get_or_create_album(self, title, artist_id):
        if not title:
            return None
        key = (title, artist_id)
        if key in self.album_cache:
            return self.album_cache[key]
        self.cur.execute(
            """SELECT album_id FROM albums
               WHERE title = %s AND album_artist_id IS NOT DISTINCT FROM %s""",
            (title, artist_id),
        )
        row = self.cur.fetchone()
        if row:
            album_id = row[0]
        else:
            self.cur.execute(
                """INSERT INTO albums (title, album_artist_id)
                   VALUES (%s, %s) RETURNING album_id""",
                (title, artist_id),
            )
            album_id = self.cur.fetchone()[0]
        self.album_cache[key] = album_id
        return album_id

    def get_or_create_track(self, name, artist_id, album_id):
        key = (name, artist_id, album_id)
        if key in self.track_cache:
            return self.track_cache[key]
        self.cur.execute(
            """SELECT track_id FROM tracks
               WHERE name = %s
                 AND artist_id IS NOT DISTINCT FROM %s
                 AND album_id IS NOT DISTINCT FROM %s""",
            (name, artist_id, album_id),
        )
        row = self.cur.fetchone()
        if row:
            track_id = row[0]
        else:
            self.cur.execute(
                """INSERT INTO tracks (name, artist_id, album_id)
                   VALUES (%s, %s, %s) RETURNING track_id""",
                (name, artist_id, album_id),
            )
            track_id = self.cur.fetchone()[0]
        self.track_cache[key] = track_id
        return track_id


def get_or_create_user(cur, display_name: str) -> int:
    cur.execute("SELECT user_id FROM users WHERE display_name = %s", (display_name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO users (display_name) VALUES (%s) RETURNING user_id",
        (display_name,),
    )
    return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# 4. Main ingestion
# ---------------------------------------------------------------------------

def ingest(df: pd.DataFrame, dsn: str, user_display_name: str):
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        user_id = get_or_create_user(cur, user_display_name)
        resolver = Resolver(cur)

        # Music rows only — this drops podcast episodes and audiobooks,
        # which use different columns (episode_name, audiobook_title, etc.)
        music_df = df[df["master_metadata_track_name"].notna()].copy()
        n_skipped_non_music = len(df) - len(music_df)

        seen_spotify_uris = set()
        event_rows = []  # collected for one bulk insert at the end
        n_skipped_no_uri = 0

        for _, row in music_df.iterrows():
            track_name = clean(row.get("master_metadata_track_name"))
            artist_name = clean(row.get("master_metadata_album_artist_name"))
            album_title = clean(row.get("master_metadata_album_album_name"))
            spotify_uri = clean(row.get("spotify_track_uri"))

            if not track_name or not spotify_uri:
                n_skipped_no_uri += 1
                continue

            artist_id = resolver.get_or_create_artist(artist_name)
            album_id = resolver.get_or_create_album(album_title, artist_id)
            track_id = resolver.get_or_create_track(track_name, artist_id, album_id)

            # spotify_tracks: one row per URI, upsert-safe
            if spotify_uri not in seen_spotify_uris:
                cur.execute(
                    """
                    INSERT INTO spotify_tracks (spotify_uri, track_id)
                    VALUES (%s, %s)
                    ON CONFLICT (spotify_uri) DO UPDATE SET
                        track_id = EXCLUDED.track_id
                    """,
                    (spotify_uri, track_id),
                )
                seen_spotify_uris.add(spotify_uri)

            event_rows.append((
                user_id,
                track_id,
                "spotify",
                spotify_uri,
                to_timestamp(row.get("ts")),
                to_int(row.get("ms_played")),
                clean(row.get("platform")),
                clean(row.get("conn_country")),
                clean(row.get("reason_start")),
                clean(row.get("reason_end")),
                to_bool(row.get("shuffle")),
                to_bool(row.get("skipped")),
                to_bool(row.get("offline")),
            ))

        # Bulk insert all listening events in one round trip
        if event_rows:
            execute_values(
                cur,
                """
                INSERT INTO listening_events (
                    user_id, track_id, source, source_track_ref,
                    played_at, ms_played, platform, conn_country,
                    reason_start, reason_end, shuffle, skipped, offline
                ) VALUES %s
                """,
                event_rows,
            )

        # Recompute aggregate stats for this user/source from the events
        # just loaded (safe to re-run: it fully replaces the aggregate row).
        cur.execute(
            """
            INSERT INTO user_track_stats (
                user_id, track_id, source, play_count, skip_count, last_played_utc
            )
            SELECT
                user_id,
                track_id,
                source,
                COUNT(*) AS play_count,
                COUNT(*) FILTER (WHERE skipped) AS skip_count,
                MAX(played_at) AS last_played_utc
            FROM listening_events
            WHERE user_id = %s AND source = 'spotify'
            GROUP BY user_id, track_id, source
            ON CONFLICT (user_id, track_id, source) DO UPDATE SET
                play_count = EXCLUDED.play_count,
                skip_count = EXCLUDED.skip_count,
                last_played_utc = EXCLUDED.last_played_utc
            """,
            (user_id,),
        )

        conn.commit()
        print(
            f"Done. Loaded {len(event_rows)} listening events for '{user_display_name}' "
            f"across {len(seen_spotify_uris)} unique tracks.\n"
            f"Skipped {n_skipped_non_music} non-music rows (podcasts/audiobooks) "
            f"and {n_skipped_no_uri} music rows missing a track name or URI."
        )

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


# ---------------------------------------------------------------------------
# 5. .env credential loading (same convention as load_apple_music.py)
# ---------------------------------------------------------------------------

def get_dsn_from_env() -> str:
    load_dotenv()
    required = ["PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD"]
    values = {key: os.getenv(key) for key in required}
    missing = [key for key, val in values.items() if not val]
    if missing:
        raise EnvironmentError(
            f"Missing required variable(s) in .env: {', '.join(missing)}. "
            f"See .env.example for the expected format."
        )
    return (
        f"postgresql://{values['PGUSER']}:{values['PGPASSWORD']}"
        f"@{values['PGHOST']}:{values['PGPORT']}/{values['PGDATABASE']}"
    )


# ---------------------------------------------------------------------------
# 6. CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Load Spotify Extended Streaming History JSON files into Postgres"
    )
    parser.add_argument(
        "--json", required=True, nargs="+",
        help="One or more JSON file paths or glob patterns, e.g. "
             "'data/spotify/alice/*.json' or file1.json file2.json",
    )
    parser.add_argument("--user", required=True, help="Display name for this history's owner")
    args = parser.parse_args()

    dsn = get_dsn_from_env()

    print(f"Loading Spotify JSON files for '{args.user}':")
    df = load_spotify_json(args.json)
    print(f"Total events parsed: {len(df)}")

    ingest(df, dsn, args.user)


if __name__ == "__main__":
    main()