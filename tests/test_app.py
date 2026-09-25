"""Headless checks of the Streamlit app pages against p_music_test. Run: uv run python tests/test_app.py"""

import io
import json
import os
import shutil
import tempfile
import zipfile
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from psycopg2.extensions import make_dsn
from streamlit.testing.v1 import AppTest

from music_project.connectors import upload
from music_project.db import add_account, get_user, list_accounts, migrate, upsert_user
from test_db import TEST_DB, run, scratch_db
from test_db_load import _item, _play, write_library
from test_hf_push import FakeHub

APP = os.path.join(os.path.dirname(__file__), "..", "app", "streamlit_app.py")
# Set before the app is first imported: app/common.py reads it once at import time.
UPLOADS = Path(tempfile.mkdtemp())
os.environ["UPLOAD_DIR"] = str(UPLOADS)
# Every app test pushes to this fake, never to the real HF dataset.
HUB = FakeHub()
upload._api = lambda: HUB


def save(at, handle):
    at.button(key=f"save_{handle}").click().run()
    assert not at.exception, at.exception


def use_test_db():
    load_dotenv()
    os.environ["DATABASE_URL"] = make_dsn(os.environ["DATABASE_URL"], dbname=TEST_DB)


def test_users_page():
    use_test_db()
    with scratch_db() as conn:
        migrate(conn)
        at = AppTest.from_file(APP, default_timeout=30).run()
        assert not at.exception, at.exception

        # New person, age entered instead of birth year.
        at.text_input(key="handle_new").input("testuser")
        at.number_input(key="age_new").set_value(25)
        at.selectbox(key="gender_new").select("female")
        at.selectbox(key="country_new").select("VN")
        at.selectbox(key="native_language_new").select("vi")
        at.selectbox(key="musical_background_new").select("self_taught")
        at.multiselect(key="instruments_new").select("guitar")
        save(at, "new")
        u = get_user(conn, "testuser")
        assert (u["birth_year"], u["gender"], u["country"], u["native_language"]) \
            == (date.today().year - 25, "female", "VN", "vi")
        assert u["musical_background"] == "self_taught" and u["instruments"] == ["guitar"]

        # The app switches to the new person; editing keeps one row and the other values.
        at.text_input(key="city_testuser").input("Hanoi")
        save(at, "testuser")
        assert run(conn, "SELECT count(*) FROM users")[0][0] == 1
        u = get_user(conn, "testuser")
        assert (u["city"], u["birth_year"], u["gender"]) == ("Hanoi", date.today().year - 25, "female")

        # Spotify first, then Apple Music, on the same person.
        for source, name in [("spotify", "tu_sp"), ("apple_music", "tu")]:
            at.selectbox(key="acct_source_testuser").select(source)
            at.text_input(key="acct_username_testuser").input(name)
            at.button(key="add_account_testuser").click().run()
            assert not at.exception and not at.error, (at.exception, at.error)
        assert {(a["source"], a["source_username"]) for a in list_accounts(conn, "testuser")} \
            == {("spotify", "tu_sp"), ("apple_music", "tu")}

        # A taken account shows an error message, not a stack trace.
        at.selectbox(key="person").select("+ New person").run()
        at.text_input(key="handle_new").input("other")
        save(at, "new")
        at.selectbox(key="acct_source_other").select("spotify")
        at.text_input(key="acct_username_other").input("tu_sp")
        at.button(key="add_account_other").click().run()
        assert not at.exception
        assert any("already linked" in e.value for e in at.error), [e.value for e in at.error]
        assert list_accounts(conn, "other") == []

        # Invalid handle: error message, nothing written.
        at.selectbox(key="person").select("+ New person").run()
        at.text_input(key="handle_new").input("Bad Handle")
        save(at, "new")
        assert at.error and get_user(conn, "Bad Handle") is None


def test_import_apple():
    use_test_db()
    with tempfile.TemporaryDirectory() as tmp, scratch_db() as conn:
        migrate(conn)
        upsert_user(conn, "tu")
        add_account(conn, "tu", "apple_music", "tu_am")
        upsert_user(conn, "nobody")
        xml = Path(tmp) / "src.xml"
        write_library(xml, [_item(1, "P1", "Hit", "A", plays=3), _item(2, "P2", "Other", "B", plays=4)])

        at = AppTest.from_file(APP, default_timeout=30).run()
        at.switch_page("pages/import_data.py").run()
        assert not at.exception, at.exception

        # A person without an Apple account is pointed at the Users page.
        at.selectbox(key="import_person").select("nobody").run()
        assert any("Users page" in i.value for i in at.info)

        at.selectbox(key="import_person").select("tu").run()
        at.get("file_uploader")[0].upload("Library.xml", xml.read_bytes(), "text/xml").run()
        at.button(key="load_apple").click().run()
        assert not at.exception and not at.error, (at.exception, [e.value for e in at.error])
        assert any("2 items" in s.value and "7 plays" in s.value for s in at.success), [s.value for s in at.success]
        assert run(conn, "SELECT sum(play_count) FROM library_items")[0][0] == 7
        assert (UPLOADS / "apple_music" / "Library_tu_am.xml").read_bytes() == xml.read_bytes()
        assert any("Pushed to Hugging Face" in s.value for s in at.success)
        assert HUB.files["raw_am/Library_tu_am.xml"] == xml.read_bytes()
        assert run(conn, "SELECT hf_path FROM ingest_runs") == [("raw_am/Library_tu_am.xml",)]

        # The same account re-uploading overwrites its own file with a new commit.
        at.button(key="load_apple").click().run()
        assert not at.warning and len([c for c in HUB.commits if c == ["raw_am/Library_tu_am.xml"]]) == 2

        # A broken file shows an error, leaves the data alone, and isn't kept as the staged copy.
        at.get("file_uploader")[0].upload("Library.xml", b"not a plist", "text/xml").run()
        at.button(key="load_apple").click().run()
        assert not at.exception and at.error
        assert run(conn, "SELECT sum(play_count) FROM library_items")[0][0] == 7
        assert (UPLOADS / "apple_music" / "Library_tu_am.xml").read_bytes() == xml.read_bytes()


def test_import_spotify():
    use_test_db()
    with scratch_db() as conn:
        migrate(conn)
        upsert_user(conn, "sp")
        add_account(conn, "sp", "spotify", "sp_sp")
        plays = [_play("2026-01-01T10:00:00Z", "spotify:track:1", "Song", "A"),
                 _play("2026-01-02T10:00:00Z", "spotify:track:2", "Other", "B")]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:  # the layout Spotify's export zip uses, plus junk
            z.writestr("Spotify Extended Streaming History/Streaming_History_Audio_2026.json", json.dumps(plays))
            z.writestr("Spotify Extended Streaming History/ReadMe.pdf", b"%PDF")
            z.writestr("../../evil.json", json.dumps(plays))

        at = AppTest.from_file(APP, default_timeout=30).run()
        at.switch_page("pages/import_data.py").run()
        at.selectbox(key="import_person").select("sp").run()
        assert any("Apple Music account" in i.value for i in at.info)  # sp only has Spotify

        uploader = lambda: at.get("file_uploader")[0]
        uploader().upload("my_spotify_data.zip", buf.getvalue(), "application/zip").run()
        at.button(key="load_spotify").click().run()
        assert not at.exception and not at.error, (at.exception, [e.value for e in at.error])
        assert any("2 plays" in s.value and "2 new" in s.value for s in at.success), [s.value for s in at.success]
        staged = UPLOADS / "spotify" / "Spotify_sp_sp"
        assert sorted(f.name for f in staged.iterdir()) == ["Streaming_History_Audio_2026.json"]
        pushed = HUB.files["raw_spot/Spotify_sp_sp/Streaming_History_Audio_2026.json"]
        assert b"ip_addr" not in pushed and b"spotify:track:1" in pushed
        assert run(conn, "SELECT hf_path FROM ingest_runs") == [("raw_spot/Spotify_sp_sp",)]
        assert not list(UPLOADS.rglob("evil.json")) and not list(UPLOADS.rglob("ReadMe.pdf"))

        # Loose JSON files work too, and only new plays are added.
        more = plays[1:] + [_play("2026-01-03T10:00:00Z", "spotify:track:3", "New", "C")]
        uploader().set_value(None).run()
        uploader().upload("Streaming_History_Audio_2026_1.json", json.dumps(more).encode(), "application/json").run()
        at.button(key="load_spotify").click().run()
        assert any("2 plays" in s.value and "1 new" in s.value for s in at.success), [s.value for s in at.success]
        assert run(conn, "SELECT count(*) FROM listening_events")[0][0] == 3
        assert len(list(staged.iterdir())) == 2

        # Broken JSON: error, nothing loaded or staged.
        uploader().set_value(None).run()
        uploader().upload("Streaming_History_Audio_2027.json", b"{not json", "application/json").run()
        at.button(key="load_spotify").click().run()
        assert not at.exception and at.error
        assert run(conn, "SELECT count(*) FROM listening_events")[0][0] == 3
        assert len(list(staged.iterdir())) == 2


def test_push_failures():
    use_test_db()
    with tempfile.TemporaryDirectory() as tmp, scratch_db() as conn:
        migrate(conn)
        upsert_user(conn, "pf")
        add_account(conn, "pf", "apple_music", "taken")
        add_account(conn, "pf", "spotify", "pf_sp")
        xml = Path(tmp) / "src.xml"
        write_library(xml, [_item(1, "P1", "Hit", "A", plays=3)])
        HUB.files["raw_am/Library_taken.xml"] = b"someone else's library"

        at = AppTest.from_file(APP, default_timeout=30).run()
        at.switch_page("pages/import_data.py").run()
        at.selectbox(key="import_person").select("pf").run()

        # Path already on HF but not pushed by this account: loaded, not pushed, other file intact.
        at.get("file_uploader")[0].upload("Library.xml", xml.read_bytes(), "text/xml").run()
        at.button(key="load_apple").click().run()
        assert not at.exception and not at.error
        assert any("already exists" in w.value for w in at.warning), [w.value for w in at.warning]
        assert HUB.files["raw_am/Library_taken.xml"] == b"someone else's library"
        assert run(conn, "SELECT count(*), count(hf_path) FROM ingest_runs")[0] == (1, 0)

        # Token without write access: loaded, clear message, and a retry that works once fixed.
        HUB.fail_status = 403
        at.get("file_uploader")[1].upload("Streaming_History_Audio_2026.json",
                                         json.dumps([_play("2026-01-01T10:00:00Z", "spotify:track:1", "S", "A")]).encode(),
                                         "application/json").run()
        at.button(key="load_spotify").click().run()
        assert any("needs write access" in w.value for w in at.warning), [w.value for w in at.warning]
        assert run(conn, "SELECT count(*) FROM listening_events")[0][0] == 1
        HUB.fail_status = None
        at.button(key="retry_push").click().run()
        assert any("Pushed to Hugging Face" in s.value for s in at.success), [s.value for s in at.success]
        assert "raw_spot/Spotify_pf_sp/Streaming_History_Audio_2026.json" in HUB.files
        assert run(conn, "SELECT hf_path FROM ingest_runs WHERE hf_path IS NOT NULL") == [("raw_spot/Spotify_pf_sp",)]


if __name__ == "__main__":
    test_users_page()
    test_import_apple()
    test_import_spotify()
    test_push_failures()
    shutil.rmtree(UPLOADS)
    print("test_app: ok")
