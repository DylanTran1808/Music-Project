"""Loader checks against p_music_test with synthetic exports. Run: uv run python tests/test_db_load.py"""

import plistlib
import tempfile
from datetime import datetime
from pathlib import Path

from music_project.db import add_account, migrate, upsert_user
from music_project.db.load import load_apple
from test_db import run, scratch_db


def _item(tid, pid, name, artist, plays=0, skips=0, **extra):
    t = {"Track ID": tid, "Persistent ID": pid, "Name": name, "Artist": artist, "Album": "Alb",
         "Genre": "Pop", "Total Time": 200_000, "Year": 2025, "Date Added": datetime(2026, 1, 1),
         "Track Type": "Remote", **extra}
    if plays:
        t["Play Count"] = plays
        t["Play Date UTC"] = datetime(2026, 7, 1)
    if skips:
        t["Skip Count"] = skips
    return t


def write_library(path, items, playlists=()):
    lib = {"Date": datetime(2026, 8, 1), "Tracks": {str(t["Track ID"]): t for t in items},
           "Playlists": [{"Name": "Library", "Master": True, "Playlist ID": 1, "Playlist Persistent ID": "PL0",
                          "Playlist Items": [{"Track ID": t["Track ID"]} for t in items]}, *playlists]}
    with open(path, "wb") as f:
        plistlib.dump(lib, f)


def counts(conn):
    tables = ["library_items", "playlists", "playlist_tracks", "tracks", "artists", "track_artists"]
    return {t: run(conn, f"SELECT count(*) FROM {t}")[0][0] for t in tables}


def test_load_apple():
    with tempfile.TemporaryDirectory() as tmp, scratch_db() as conn:
        migrate(conn)
        upsert_user(conn, "kien")
        acct = add_account(conn, "kien", "apple_music", "kien")
        xml = Path(tmp) / "Library_kien.xml"
        write_library(xml, [
            _item(10, "P1", "Hit (feat. B)", "A & C", plays=10, Loved=True),
            _item(11, "P2", "Hit", "A", plays=5),                       # same song added twice
            _item(12, "P3", "Hit (Remix)", "A", plays=2),               # a version of it
            _item(13, "P4", "Other", "D", skips=3, **{"Playlist Only": True}),
        ], [{"Name": "Mine", "Playlist ID": 2, "Playlist Persistent ID": "PL1",
             "Playlist Items": [{"Track ID": 13}, {"Track ID": 10}]}])

        result = load_apple(conn, acct, xml)
        run_id = result["run_id"]
        assert (result["items"], result["plays"]) == (4, 17)

        # Every item kept (no overwrite), all pointing at one shared track per song.
        assert run(conn, "SELECT sum(play_count), count(*) FROM library_items")[0] == (17, 4)
        assert run(conn, "SELECT count(DISTINCT track_id) FROM library_items")[0][0] == 2
        assert run(conn, """SELECT t.match_key, i.version_tags FROM library_items i JOIN tracks t ON t.id = i.track_id
                            WHERE i.apple_persistent_id = 'P3'""") == [("a|hit", ["remix"])]
        assert run(conn, "SELECT loved, playlist_only, skip_count FROM library_items WHERE apple_persistent_id IN ('P1', 'P4')"
                         " ORDER BY apple_persistent_id") == [(True, False, 0), (False, True, 3)]

        # Credits split, in order: primary artist, other credited artist, featured artist.
        assert sorted(r[0] for r in run(conn, "SELECT name FROM artists")) == ["A", "B", "C", "D"]
        assert run(conn, """SELECT array_agg(a.name ORDER BY ta.position) FROM track_artists ta
                            JOIN artists a ON a.id = ta.artist_id JOIN tracks t ON t.id = ta.track_id
                            WHERE t.match_key = 'a|hit'""") == [(["A", "C", "B"],)]

        # Playlists keep their order and curated flag.
        assert run(conn, """SELECT p.name, p.is_user_curated, array_agg(i.apple_persistent_id ORDER BY pt.position)
                            FROM playlists p JOIN playlist_tracks pt ON pt.playlist_id = p.id
                            JOIN library_items i ON i.id = pt.library_item_id GROUP BY p.id ORDER BY p.name""") \
            == [("Library", False, ["P1", "P2", "P3", "P4"]), ("Mine", True, ["P4", "P1"])]

        assert run(conn, "SELECT account_id, row_count, length(file_hash) FROM ingest_runs WHERE id = %s",
                   (run_id,)) == [(acct, 4, 64)]

        # Reloading the same file changes nothing but the ingest log.
        before = counts(conn)
        load_apple(conn, acct, xml)
        assert counts(conn) == before
        assert run(conn, "SELECT count(*) FROM ingest_runs")[0][0] == 2

        # A second person with the same song shares the track row.
        upsert_user(conn, "kha")
        acct2 = add_account(conn, "kha", "apple_music", "kha")
        xml2 = Path(tmp) / "Library_kha.xml"
        write_library(xml2, [_item(1, "Q1", "HIT", "a", plays=4)])
        load_apple(conn, acct2, xml2)
        assert run(conn, "SELECT count(*) FROM tracks")[0][0] == before["tracks"]
        assert run(conn, "SELECT count(*) FROM library_items")[0][0] == 5

        # An export with no tracks is refused instead of wiping the previous load.
        empty = Path(tmp) / "empty.xml"
        write_library(empty, [])
        try:
            load_apple(conn, acct2, empty)
            raise AssertionError("loaded an empty library")
        except ValueError:
            pass
        assert run(conn, "SELECT count(*) FROM library_items WHERE account_id = %s", (acct2,))[0][0] == 1

        # Only apple_music accounts can take an Apple library.
        sp = add_account(conn, "kha", "spotify", "kha_sp")
        try:
            load_apple(conn, sp, xml2)
            raise AssertionError("loaded an Apple library into a Spotify account")
        except ValueError:
            pass


if __name__ == "__main__":
    test_load_apple()
    print("test_db_load: ok")
