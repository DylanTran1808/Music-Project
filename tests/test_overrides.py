"""Committed catalogue fixes (overrides/*.json) survive a rebuild. Run: uv run python tests/test_overrides.py"""

import json
import tempfile
from pathlib import Path

from music_project.db import migrate
from music_project.db.artists import set_artist_demographics
from music_project.db.overrides import apply_overrides, export_artists
from test_db import run, scratch_db


def credits(conn, match_key):
    return run(conn, """SELECT array_agg(a.name ORDER BY ta.position) FROM track_artists ta JOIN artists a ON a.id = ta.artist_id
                        JOIN tracks t ON t.id = ta.track_id WHERE t.match_key = %s""", (match_key,))[0][0]


def seed(conn):
    """A show credited as the artist: once with the performers as feat., once with nobody."""
    for key, title, names in [("rap viet|anh van ok", "Anh Vẫn OK (feat. RPT MCK)", ["RAP VIỆT", "RPT MCK"]),
                              ("em xinh|aaa", "AAA", ["EM XINH"])]:
        tid = run(conn, "INSERT INTO tracks (match_key, song_key, title) VALUES (%s, %s, %s) RETURNING id", (key, key, title))[0][0]
        for pos, name in enumerate(names):
            run(conn, "INSERT INTO artists (name) VALUES (%s) ON CONFLICT DO NOTHING", (name,))
            run(conn, "INSERT INTO track_artists (track_id, artist_id, position) SELECT %s, id, %s FROM artists WHERE name = %s",
                (tid, pos, name))
    run(conn, "INSERT INTO artists (name) VALUES ('Tiên Tiên')")


def test_overrides_round_trip():
    with tempfile.TemporaryDirectory() as tmp, scratch_db() as conn:
        d = Path(tmp)
        (d / "credits.json").write_text(json.dumps([
            {"match_key": "rap viet|anh van ok", "program": "RAP VIỆT", "performers": ["RPT MCK"],
             "source_url": "from the release's feat. credits"},
            {"match_key": "em xinh|aaa", "program": "EM XINH", "performers": ["Tiên Tiên", "Châu Bùi"],
             "source_url": "https://www.elle.vn/the-gioi-van-hoa/aaa-tiet-muc-an-tuong-em-xinh-say-hi/"},
            {"match_key": "not|in this db", "program": "EM XINH", "performers": ["X"], "source_url": "-"},
        ], ensure_ascii=False), encoding="utf-8")
        migrate(conn)
        seed(conn)
        set_artist_demographics(conn, run(conn, "SELECT id FROM artists WHERE name = 'Tiên Tiên'")[0][0], "web", status="web",
                                artist_type="person", gender="female", country="VN", begin_date="1990-02-23",
                                source_url="https://example.org/tien-tien")

        # Export the researched artist facts, then rebuild from scratch and re-apply everything.
        assert export_artists(conn, d / "artists.json") == 1
        run(conn, "DROP SCHEMA public CASCADE")
        run(conn, "CREATE SCHEMA public")
        migrate(conn)
        seed(conn)
        for _ in range(2):  # idempotent
            stats = apply_overrides(conn, d)
        assert stats == {"credits": 2, "credits_missing": 1, "artists": 1, "artists_missing": 0}, stats

        # Performers first, the show last; new performers are created.
        assert credits(conn, "rap viet|anh van ok") == ["RPT MCK", "RAP VIỆT"]
        assert credits(conn, "em xinh|aaa") == ["Tiên Tiên", "Châu Bùi", "EM XINH"]
        # Shows are marked as programmes, not people, and leave the review queue.
        assert run(conn, "SELECT artist_type, tags, match_status FROM artists WHERE name = 'EM XINH'") \
            == [("other", ["tv show"], "web")]
        # Researched facts are back.
        assert run(conn, "SELECT gender, country, begin_date::text, match_status, source_url FROM artists WHERE name = 'Tiên Tiên'") \
            == [("female", "VN", "1990-02-23", "web", "https://example.org/tien-tien")]


if __name__ == "__main__":
    test_overrides_round_trip()
    print("test_overrides: ok")
