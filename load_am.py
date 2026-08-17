"""
Load an Apple Music "Library.xml" export into the multi-source music database.

Populates: artists, albums, tracks, apple_music_tracks, user_track_stats
(users.display_name must already exist, or is created on the fly)

Usage:
    python load_apple_music.py --xml /path/to/Library.xml --user "Me" --dsn "postgresql://user:pass@localhost:5432/music"

Requires:
    pip install psycopg2-binary pandas
"""

import argparse
import math
import plistlib
from datetime import datetime, timezone

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values


# ---------------------------------------------------------------------------
# 1. Parse Library.xml -> DataFrame
# ---------------------------------------------------------------------------

def load_apple_xml(xml_path: str) -> pd.DataFrame:
    """Parse Apple Music's exported Library.xml into a flat DataFrame,
    one row per track, columns named like Apple's XML keys."""
    with open(xml_path, "rb") as f:
        plist = plistlib.load(f)

    tracks_dict = plist.get("Tracks", {})
    df = pd.DataFrame.from_dict(tracks_dict, orient="index")
    df = df.reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# 2. Helpers to clean values coming out of the DataFrame
# ---------------------------------------------------------------------------

def clean(val):
    """Turn NaN/NaT into None; leave everything else as-is."""
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    if pd.isna(val) if not isinstance(val, (list, dict)) else False:
        return None
    return val


def to_bool(val):
    """Apple XML booleans usually arrive as native True/False (plistlib),
    but pandas may show them as 'Yes'/NaN too depending on how the frame
    was built. Normalize both cases; anything present = True."""
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


def to_float(val):
    val = clean(val)
    return float(val) if val is not None else None


def to_timestamp(val):
    val = clean(val)
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    try:
        return pd.to_datetime(val)
    except Exception:
        return None


def to_date(val):
    ts = to_timestamp(val)
    return ts.date() if ts is not None else None


# ---------------------------------------------------------------------------
# 3. Get-or-create helpers (canonical identity tables)
# ---------------------------------------------------------------------------

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


def get_or_create_artist(cur, name, sort_name):
    if not name:
        return None
    cur.execute("SELECT artist_id FROM artists WHERE name = %s", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        """INSERT INTO artists (name, sort_name)
           VALUES (%s, %s) RETURNING artist_id""",
        (name, sort_name),
    )
    return cur.fetchone()[0]


def get_or_create_album(cur, title, album_artist_id, release_date, sort_album, compilation):
    if not title:
        return None
    cur.execute(
        """SELECT album_id FROM albums
           WHERE title = %s AND album_artist_id IS NOT DISTINCT FROM %s""",
        (title, album_artist_id),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        """INSERT INTO albums (title, album_artist_id, release_date, sort_album, compilation)
           VALUES (%s, %s, %s, %s, %s) RETURNING album_id""",
        (title, album_artist_id, release_date, sort_album, compilation),
    )
    return cur.fetchone()[0]


def get_or_create_track(cur, name, sort_name, artist_id, album_id, composer, genre,
                         track_number, disc_number, year, explicit):
    """Match an existing canonical track by (name, artist_id, album_id);
    insert a new one if this combination hasn't been seen from any source yet."""
    cur.execute(
        """SELECT track_id FROM tracks
           WHERE name = %s
             AND artist_id IS NOT DISTINCT FROM %s
             AND album_id IS NOT DISTINCT FROM %s""",
        (name, artist_id, album_id),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        """INSERT INTO tracks
           (name, sort_name, artist_id, album_id, composer, genre,
            track_number, disc_number, year, explicit)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING track_id""",
        (name, sort_name, artist_id, album_id, composer, genre,
         track_number, disc_number, year, explicit),
    )
    return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# 4. Main ingestion loop
# ---------------------------------------------------------------------------

def ingest(df: pd.DataFrame, dsn: str, user_display_name: str):
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        user_id = get_or_create_user(cur, user_display_name)

        n_inserted, n_updated, n_skipped = 0, 0, 0

        for _, row in df.iterrows():
            name = clean(row.get("Name"))
            persistent_id = clean(row.get("Persistent ID"))

            if not name or not persistent_id:
                # Can't meaningfully store a track with no name or no stable ID
                n_skipped += 1
                continue

            artist_name = clean(row.get("Artist"))
            album_artist_name = clean(row.get("Album Artist")) or artist_name
            sort_artist = clean(row.get("Sort Artist"))
            album_title = clean(row.get("Album"))
            sort_album = clean(row.get("Sort Album"))
            release_date = to_date(row.get("Release Date"))
            compilation = to_bool(row.get("Compilation"))

            # --- canonical identity ---
            album_artist_id = get_or_create_artist(cur, album_artist_name, sort_artist)
            # If the track artist differs from the album artist (e.g. features/compilations),
            # still resolve its own artist row for the track-level FK.
            track_artist_id = (
                get_or_create_artist(cur, artist_name, sort_artist)
                if artist_name and artist_name != album_artist_name
                else album_artist_id
            )

            album_id = get_or_create_album(
                cur, album_title, album_artist_id, release_date, sort_album, compilation
            )

            track_id = get_or_create_track(
                cur,
                name=name,
                sort_name=clean(row.get("Sort Name")),
                artist_id=track_artist_id,
                album_id=album_id,
                composer=clean(row.get("Composer")),
                genre=clean(row.get("Genre")),
                track_number=to_int(row.get("Track Number")),
                disc_number=to_int(row.get("Disc Number")),
                year=to_int(row.get("Year")),
                explicit=to_bool(row.get("Explicit")),
            )

            # --- source-specific metadata (apple_music_tracks) ---
            cur.execute(
                """
                INSERT INTO apple_music_tracks (
                    persistent_id, apple_track_id, track_id,
                    kind, size_bytes, total_time_ms, bit_rate, sample_rate,
                    location, date_added, date_modified,
                    protected, playlist_only, part_of_gapless_album
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (persistent_id) DO UPDATE SET
                    apple_track_id = EXCLUDED.apple_track_id,
                    track_id = EXCLUDED.track_id,
                    kind = EXCLUDED.kind,
                    size_bytes = EXCLUDED.size_bytes,
                    total_time_ms = EXCLUDED.total_time_ms,
                    bit_rate = EXCLUDED.bit_rate,
                    sample_rate = EXCLUDED.sample_rate,
                    location = EXCLUDED.location,
                    date_added = EXCLUDED.date_added,
                    date_modified = EXCLUDED.date_modified,
                    protected = EXCLUDED.protected,
                    playlist_only = EXCLUDED.playlist_only,
                    part_of_gapless_album = EXCLUDED.part_of_gapless_album
                """,
                (
                    persistent_id,
                    to_int(row.get("Track ID")),
                    track_id,
                    clean(row.get("Kind")),
                    to_int(row.get("Size")),
                    to_int(row.get("Total Time")),
                    to_int(row.get("Bit Rate")),
                    to_int(row.get("Sample Rate")),
                    clean(row.get("Location")),
                    to_timestamp(row.get("Date Added")),
                    to_timestamp(row.get("Date Modified")),
                    to_bool(row.get("Protected")),
                    to_bool(row.get("Playlist Only")),
                    to_bool(row.get("Part Of Gapless Album")),
                ),
            )

            # --- per-user behavioral stats (user_track_stats) ---
            play_count = to_int(row.get("Play Count"))
            skip_count = to_int(row.get("Skip Count"))
            last_played = to_timestamp(row.get("Play Date UTC"))
            loved = to_bool(row.get("Loved"))
            favorited = to_bool(row.get("Favorited"))

            cur.execute(
                """
                INSERT INTO user_track_stats (
                    user_id, track_id, source,
                    play_count, skip_count, last_played_utc, loved, favorited
                )
                VALUES (%s, %s, 'apple_music', %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, track_id, source) DO UPDATE SET
                    play_count = EXCLUDED.play_count,
                    skip_count = EXCLUDED.skip_count,
                    last_played_utc = EXCLUDED.last_played_utc,
                    loved = EXCLUDED.loved,
                    favorited = EXCLUDED.favorited
                """,
                (user_id, track_id, play_count, skip_count, last_played, loved, favorited),
            )

            n_inserted += 1

        conn.commit()
        print(f"Done. Processed {n_inserted} tracks for user '{user_display_name}' "
              f"(skipped {n_skipped} rows missing Name/Persistent ID).")

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


# ---------------------------------------------------------------------------
# 5. CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Load Apple Music Library.xml into Postgres")
    parser.add_argument("--xml", required=True, help="Path to Apple Music Library.xml")
    parser.add_argument("--user", required=True, help="Display name for this library's owner")
    parser.add_argument("--dsn", required=True,
                         help="Postgres connection string, e.g. postgresql://user:pass@host:5432/dbname")
    args = parser.parse_args()

    df = load_apple_xml(args.xml)
    print(f"Parsed {len(df)} tracks from {args.xml}")
    ingest(df, args.dsn, args.user)


if __name__ == "__main__":
    main()