"""Headless check of the Streamlit app's Users page against p_music_test. Run: uv run python tests/test_app.py"""

import os
from datetime import date

from dotenv import load_dotenv
from psycopg2.extensions import make_dsn
from streamlit.testing.v1 import AppTest

from music_project.db import get_user, list_accounts, migrate
from test_db import TEST_DB, run, scratch_db

APP = os.path.join(os.path.dirname(__file__), "..", "app", "streamlit_app.py")


def save(at, handle):
    at.button(key=f"save_{handle}").click().run()
    assert not at.exception, at.exception


def test_users_page():
    load_dotenv()
    os.environ["DATABASE_URL"] = make_dsn(os.environ["DATABASE_URL"], dbname=TEST_DB)
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


if __name__ == "__main__":
    test_users_page()
    print("test_app: ok")
