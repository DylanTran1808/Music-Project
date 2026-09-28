"""Language groups for artists and tracks (vi / en / other / unknown). Run: uv run python tests/test_languages.py"""

from music_project.db import migrate
from music_project.db.languages import artist_language, classify_languages
from test_db import run, scratch_db


def test_artist_language():
    lang = lambda **kw: artist_language(**{"country": None, "release_languages": [], "scripts": {},
                                           "vi_collaborator": False, "detected": None, **kw})
    # Vietnamese: country, release language, any Vietnamese-script title, or credited with a Vietnamese artist.
    assert lang(country="VN") == "vi"
    assert lang(release_languages=["vi", "en"]) == "vi"
    assert lang(scripts={"latin": 3, "vi": 1}) == "vi"
    assert lang(scripts={"latin": 2}, vi_collaborator=True) == "vi"
    # East Asian artists are "other" even when their titles / releases read as English (K-pop).
    assert lang(country="KR", release_languages=["en", "ko"]) == "other"
    assert lang(scripts={"ko": 2, "latin": 1}) == "other"
    assert lang(release_languages=["ja"]) == "other"
    assert lang(release_languages=["fr", "en"], country="FR") == "other"
    # English: English releases (any country: Swedish EDM sings in English) or an English-speaking country.
    assert lang(release_languages=["en"], country="SE") == "en"
    assert lang(country="US") == "en"
    # No MusicBrainz data: trust langdetect only when it's sure it's English.
    assert lang(scripts={"latin": 2}, detected=("en", 0.99)) == "en"
    assert lang(scripts={"latin": 2}, detected=("en", 0.6)) == "unknown"
    assert lang(scripts={"latin": 1}, detected=("de", 1.0)) == "unknown"  # "SLOMO" -> German: not trusted
    assert lang() == "unknown"


def test_classify_languages():
    with scratch_db() as conn:
        migrate(conn)

        def track(key, title, lang, *artists):
            tid = run(conn, "INSERT INTO tracks (match_key, song_key, title, lang) VALUES (%s, %s, %s, %s) RETURNING id",
                      (key, key, title, lang))[0][0]
            for pos, a in enumerate(artists):
                aid = run(conn, "INSERT INTO artists (name) VALUES (%s) ON CONFLICT DO NOTHING RETURNING id", (a,))
                aid = aid[0][0] if aid else run(conn, "SELECT id FROM artists WHERE name = %s", (a,))[0][0]
                run(conn, "INSERT INTO track_artists (track_id, artist_id, position) VALUES (%s, %s, %s)", (tid, aid, pos))

        track("obito|xa xoi", "Xa Xôi", "vi", "Obito", "Teddie J")
        track("teddie j|slomo", "SLOMO", "latin", "Teddie J")          # unaccented title, Vietnamese rapper
        track("taylor|hit", "Hit", "latin", "Taylor")
        track("bp|how you like that", "How You Like That", "latin", "BP")
        track("bp|사랑", "사랑", "ko", "BP")
        track("x|vui", "Vui", "latin", "Unknown Guy")
        run(conn, "UPDATE artists SET country = 'US', release_languages = '{en}' WHERE name = 'Taylor'")
        run(conn, "UPDATE artists SET country = 'KR', release_languages = '{en,ko}' WHERE name = 'BP'")

        classify_languages(conn)
        assert dict(run(conn, "SELECT name, lang_group FROM artists")) == {
            "Obito": "vi", "Teddie J": "vi", "Taylor": "en", "BP": "other", "Unknown Guy": "unknown"}
        assert dict(run(conn, "SELECT title, lang_group FROM tracks")) == {
            "Xa Xôi": "vi", "SLOMO": "vi", "Hit": "en", "How You Like That": "other", "사랑": "other", "Vui": "unknown"}

        # Idempotent, and picks up changed artist data on the next run.
        run(conn, "UPDATE artists SET country = 'VN' WHERE name = 'Unknown Guy'")
        classify_languages(conn)
        assert run(conn, "SELECT lang_group FROM tracks WHERE title = 'Vui'") == [("vi",)]


if __name__ == "__main__":
    test_artist_language()
    test_classify_languages()
    print("test_languages: ok")
