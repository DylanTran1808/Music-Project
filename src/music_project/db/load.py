"""
Loaders: source exports -> p_music.

Parsing, artist-credit splitting and match keys come from the analysis modules,
so the DB and the notebooks agree on what a "track" is. Each load replaces the
account's rows for that source in one transaction and logs an ingest_runs row,
so loading the same export twice changes nothing.
"""

import hashlib
import plistlib
from pathlib import Path

import pandas as pd
from psycopg2.extras import execute_values

from music_project.analysis.apple_insights import load_library


def _v(x):
    """pandas/numpy scalar -> plain Python value psycopg2 can adapt (NaN/NaT/NA -> None)."""
    if isinstance(x, (list, tuple)):
        return list(x)
    if pd.isna(x):
        return None
    return x.item() if hasattr(x, "item") else x


def _check_account(cur, account_id, source):
    cur.execute("SELECT source FROM user_accounts WHERE id = %s", (account_id,))
    row = cur.fetchone()
    if row is None or row[0] != source:
        raise ValueError(f"account {account_id} is not a {source} account")


def upsert_catalog(cur, tracks: pd.DataFrame) -> dict:
    """
    Inserts missing artists, tracks and credits; returns {match_key: track_id}.
    `tracks`: one row per match_key with title, album, duration_ms, genre, release_year,
    lang, artists (ordered list). Existing tracks keep their metadata and credits.
    """
    names = list(dict.fromkeys(a for lst in tracks["artists"] for a in lst))
    execute_values(cur, "INSERT INTO artists (name) VALUES %s ON CONFLICT DO NOTHING", [(n,) for n in names])
    # Resolve through SQL lower() so the lookup matches the unique index exactly.
    cur.execute("SELECT x, a.id FROM unnest(%s::text[]) x JOIN artists a ON lower(a.name) = lower(x)", (names,))
    artist_id = dict(cur.fetchall())

    cols = ["match_key", "title", "album", "duration_ms", "genre", "release_year", "lang"]
    execute_values(cur, f"INSERT INTO tracks ({', '.join(cols)}) VALUES %s ON CONFLICT (match_key) DO NOTHING",
                   [tuple(_v(r[c]) for c in cols) for _, r in tracks.iterrows()])
    cur.execute("SELECT match_key, id FROM tracks WHERE match_key = ANY(%s)", (list(tracks["match_key"]),))
    track_id = dict(cur.fetchall())

    credits = [(track_id[k], artist_id[a], pos)
               for k, lst in zip(tracks["match_key"], tracks["artists"]) for pos, a in enumerate(lst)]
    execute_values(cur, "INSERT INTO track_artists (track_id, artist_id, position) VALUES %s ON CONFLICT DO NOTHING",
                   credits)
    return track_id


def load_apple(conn, account_id: int, xml_path) -> dict:
    """Loads an Apple Music Library.xml into an apple_music account; returns {run_id, items, plays}."""
    xml_path = Path(xml_path)
    with open(xml_path, "rb") as f:  # parse + sanity-check before touching the DB
        if not plistlib.load(f).get("Tracks"):
            raise ValueError("no tracks in this file; is it an Apple Music Library.xml export?")
    lib = load_library(str(account_id), path=str(xml_path))
    t = lib.tracks

    # Catalogue: one row per song; the unversioned, most-played item names it.
    canon = (t.assign(_versioned=t["version_tags"].str.len() > 0)
              .sort_values(["_versioned", "play_count"], ascending=[True, False])
              .drop_duplicates("track_key"))
    catalog = pd.DataFrame({
        "match_key": canon["track_key"], "title": canon["name"].fillna(""), "album": canon["album"],
        "duration_ms": pd.to_numeric(canon["total_time_ms"], errors="coerce").round().astype("Int64"),
        "genre": canon["genre"], "release_year": canon["year"], "lang": canon["script_lang"],
        "artists": canon["artists"],
    })

    with conn, conn.cursor() as cur:
        _check_account(cur, account_id, "apple_music")
        track_id = upsert_catalog(cur, catalog)

        cur.execute("DELETE FROM playlists WHERE account_id = %s", (account_id,))  # cascades to playlist_tracks
        cur.execute("DELETE FROM library_items WHERE account_id = %s", (account_id,))

        items = execute_values(cur, """
            INSERT INTO library_items (account_id, track_id, apple_persistent_id, title, artist_credit, album,
                                       play_count, skip_count, loved, playlist_only, date_added, last_played,
                                       last_skipped, version_tags)
            VALUES %s RETURNING apple_persistent_id, id""", [
            (account_id, track_id[r.track_key], r.persistent_id, _v(r.name), _v(r.artist), _v(r.album),
             int(r.play_count), int(r.skip_count), bool(r.is_loved), bool(r.playlist_only),
             _v(r.date_added), _v(r.play_date_utc), _v(r.skip_date), list(r.version_tags))
            for r in t.itertuples()], fetch=True)
        item_id = dict(items)
        by_export_id = {tid: item_id[pid] for tid, pid in zip(t["track_id"], t["persistent_id"])}

        p = lib.playlists
        heads = p.drop_duplicates("playlist_persistent_id")
        playlists = execute_values(cur, """
            INSERT INTO playlists (account_id, apple_persistent_id, name, description, is_user_curated)
            VALUES %s RETURNING apple_persistent_id, id""", [
            (account_id, r.playlist_persistent_id, _v(r.playlist_name), _v(r.description) or None,
             bool(r.is_user_curated)) for r in heads.itertuples()], fetch=True) if len(heads) else []
        playlist_id = dict(playlists)
        members = [(playlist_id[r.playlist_persistent_id], int(r.position), by_export_id[r.track_id])
                   for r in p.itertuples() if r.track_id in by_export_id]
        if members:
            execute_values(cur, "INSERT INTO playlist_tracks (playlist_id, position, library_item_id) VALUES %s",
                           members)

        cur.execute("INSERT INTO ingest_runs (account_id, file_hash, row_count) VALUES (%s, %s, %s) RETURNING id",
                    (account_id, hashlib.sha256(xml_path.read_bytes()).hexdigest(), len(t)))
        return {"run_id": cur.fetchone()[0], "items": len(t), "plays": int(t["play_count"].sum())}
