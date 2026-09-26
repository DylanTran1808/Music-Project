"""
Artist demographics checks: MusicBrainz / Wikidata parsing, the match rule, and the enrichment loop
on p_music_test. No network: tests/fixtures/artist_lookups.json holds real responses (recorded
2026-09-26, trimmed to the fields the code reads). Run: uv run python tests/test_musicbrainz.py
"""

import json
from datetime import date
from pathlib import Path

from music_project.connectors import musicbrainz, wikidata
from music_project.connectors.musicbrainz import artist_fields, pick_match, release_summary
from music_project.connectors.wikidata import is_musical, parse_candidates
from music_project.db import migrate
from music_project.db.artists import coverage, enrich_artists, review_items, set_artist_demographics
from test_db import run, scratch_db

FX = json.loads((Path(__file__).parent / "fixtures" / "artist_lookups.json").read_text(encoding="utf-8"))
SEARCH, RELEASES = FX["search"], FX["releases"]
SON_TUNG, BLACKPINK, TRINH = (SEARCH[n][0] for n in ["Sơn Tùng M-TP", "BLACKPINK", "Trịnh Công Sơn"])
WREN = SEARCH["Wren"]


def releases_of(mb_id):
    return RELEASES.get(mb_id, [])


def test_release_summary():
    # 11 vie + 2 eng + 2 unlabelled -> most frequent first, ISO 639-1.
    assert release_summary(RELEASES[SON_TUNG["id"]]) == {"release_languages": ["vi", "en"], "first_release_year": 2016}
    # "mul" (multiple) is not a language.
    assert release_summary(RELEASES[BLACKPINK["id"]])["release_languages"] == ["en", "ko"]
    assert release_summary(RELEASES[TRINH["id"]]) == {"release_languages": ["vi"], "first_release_year": 1994}
    assert release_summary([]) == {"release_languages": [], "first_release_year": None}


def test_artist_fields():
    f = artist_fields(SON_TUNG)
    assert (f["artist_type"], f["gender"], f["country"], f["area"], f["birth_area"]) \
        == ("person", "male", "VN", "Vietnam", "Thái Bình")
    assert (f["begin_date"], f["end_date"], f["mb_id"]) == (date(1994, 7, 5), None, SON_TUNG["id"])
    assert f["tags"][:2] == ["male vocalist", "vietnamese"]  # by vote count
    g = artist_fields(BLACKPINK)
    assert (g["artist_type"], g["gender"], g["country"], g["begin_date"]) == ("group", "not_applicable", "KR", date(2016, 8, 8))
    assert artist_fields(TRINH)["end_date"] == date(2001, 4, 1)
    # Partial dates keep the year: "1994" -> 1994-01-01.
    assert artist_fields({"id": "x", "life-span": {"begin": "1994"}})["begin_date"] == date(1994, 1, 1)


def test_pick_match():
    assert pick_match("Sơn Tùng M-TP", [SON_TUNG], [], releases_of) == ("auto", SON_TUNG)
    assert pick_match("sơn tùng m-tp", [SON_TUNG], [], releases_of)[0] == "auto"  # case-insensitive
    assert pick_match("Nobody", [], [], releases_of) == ("not_found", None)
    assert pick_match("Sơn Tùng", [SON_TUNG], [], releases_of) == ("not_found", None)  # no same-name artist
    # Several artists named "Wren": an album we know picks the right one...
    assert pick_match("Wren", WREN, ["Thrall"], releases_of) == ("auto", WREN[0])
    assert pick_match("Wren", WREN, ["no more"], releases_of) == ("auto", WREN[1])
    # ...otherwise it's the user's call.
    assert pick_match("Wren", WREN, ["Some Other Album"], releases_of) == ("ambiguous", None)
    assert pick_match("Wren", WREN, [], releases_of) == ("ambiguous", None)


def test_wikidata_candidates():
    hh = FX["wikidata"]["Hiền Hồ"]
    got = parse_candidates(hh["search"], hh["entities"], hh["refs"])
    # Only the singer: the prince, the researchers, the politician and the hotel are dropped.
    assert got == [{"wikidata_id": "Q108759374", "label": "Hien Ho", "description": "Vietnamese singer",
                    "instance_of": ["human"], "gender": "female", "country": "VN", "birth_date": "1997-02-26",
                    "birth_place": "Đắk Lắk", "end_date": None, "mb_id": None}], got
    w = FX["wikidata"]["Wren"]
    assert parse_candidates(w["search"], w["entities"], w["refs"]) == []  # a town, birds, a video game
    # Real descriptions from the first full run: only the boy group is a musician.
    keep = lambda desc, types=(): is_musical({"description": desc, "instance_of": list(types), "mb_id": None})
    assert keep("Vietnamese boy group") and keep("Japanese music composer") and keep("singer-songwriter")
    assert keep("", ["musical group"]) and keep("Vietnamese singer, rapper and dancer (born 1984)")
    assert not keep("group of organisms which mostly grow in water and can perform oxygenic photosynthesis")
    assert not keep("any single member of Homo sapiens, unique extant species of the genus Homo")
    assert not keep("single") and not keep("2022 single by Monstar") and not keep("family name")


def test_enrich_artists():
    calls = []

    def fake_search(name):
        calls.append(name)
        if name == "Boom":
            raise ConnectionError("network down")
        return SEARCH.get(name, [])

    def fake_wikidata(name):
        hh = FX["wikidata"]["Hiền Hồ"]
        return parse_candidates(hh["search"], hh["entities"], hh["refs"]) if name == "Hiền Hồ" else []

    musicbrainz.search_artist, musicbrainz.artist_releases, wikidata.search_wikidata = \
        fake_search, releases_of, fake_wikidata
    with scratch_db() as conn:
        migrate(conn)
        ids = {n: run(conn, "INSERT INTO artists (name) VALUES (%s) RETURNING id", (n,))[0][0]
               for n in ["Sơn Tùng M-TP", "BLACKPINK", "Trịnh Công Sơn", "Wren", "Hiền Hồ", "Boom", "Hand Made"]}
        set_artist_demographics(conn, ids["Hand Made"], "user", gender="female", country="VN")

        stats = enrich_artists(conn)
        assert stats == {"auto": 3, "ambiguous": 1, "not_found": 1, "errors": 1}, stats
        assert "Hand Made" not in calls  # manual rows are never looked up

        row = lambda n: run(conn, """SELECT match_status, demographics_source, artist_type, gender, country,
                                            release_languages, first_release_year, fetched_at IS NOT NULL
                                     FROM artists WHERE id = %s""", (ids[n],))[0]
        assert row("Sơn Tùng M-TP") == ("auto", "musicbrainz", "person", "male", "VN", ["vi", "en"], 2016, True)
        assert row("BLACKPINK")[:5] == ("auto", "musicbrainz", "group", "not_applicable", "KR")
        assert row("Wren")[0] == "ambiguous" and row("Hiền Hồ")[0] == "not_found"
        assert row("Boom")[7] is False  # failed lookup: retried next run
        assert row("Hand Made")[:5] == ("manual", "user", None, "female", "VN")

        # Candidates kept for review: MusicBrainz hits for Wren, the Wikidata singer for Hiền Hồ.
        wren = run(conn, "SELECT candidates FROM artists WHERE id = %s", (ids["Wren"],))[0][0]
        assert [c["mb_id"] for c in wren["musicbrainz"]][:2] == [WREN[0]["id"], WREN[1]["id"]]
        hh = run(conn, "SELECT candidates FROM artists WHERE id = %s", (ids["Hiền Hồ"],))[0][0]
        assert [c["wikidata_id"] for c in hh["wikidata"]] == ["Q108759374"]

        # Ages from the view: living person, deceased person, group, undated.
        today = date.today()
        age = lambda born, until: until.year - born.year - ((until.month, until.day) < (born.month, born.day))
        view = dict(run(conn, "SELECT name, age FROM artist_demographics"))
        assert view["Sơn Tùng M-TP"] == age(date(1994, 7, 5), today)
        assert view["Trịnh Công Sơn"] == age(date(1939, 2, 28), date(2001, 4, 1)) == 62
        assert view["BLACKPINK"] is None and view["Wren"] is None
        assert dict(run(conn, "SELECT name, years_active FROM artist_demographics"))["BLACKPINK"] \
            == age(date(2016, 8, 8), today)

        # Reports: the status split, and the review queue (unclear matches with their candidates).
        assert {r["status"]: r["artists"] for r in coverage(conn)} \
            == {"auto": 3, "ambiguous": 1, "not_found": 1, "manual": 1, "pending": 1}
        queue = review_items(conn)
        assert [(r["name"], r["match_status"]) for r in queue] == [("Hiền Hồ", "not_found"), ("Wren", "ambiguous")]
        assert queue[0]["candidates"]["wikidata"][0]["label"] == "Hien Ho" and queue[1]["albums"] == []

        # Re-run: only the failed artist is looked up again; the manual row is still untouched.
        calls.clear()
        musicbrainz.search_artist = lambda name: calls.append(name) or SEARCH.get(name, [])
        assert enrich_artists(conn) == {"auto": 0, "ambiguous": 0, "not_found": 1, "errors": 0}
        assert calls == ["Boom"]
        assert row("Hand Made")[:5] == ("manual", "user", None, "female", "VN")


if __name__ == "__main__":
    test_release_summary()
    test_artist_fields()
    test_pick_match()
    test_wikidata_candidates()
    test_enrich_artists()
    print("test_musicbrainz: ok")
