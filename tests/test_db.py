"""DB checks against a throwaway p_music_test database. Run: uv run python tests/test_db.py"""

import tempfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import psycopg2

from music_project.db import (
    account_pushed, add_account, birth_year_from_age, connect, get_user, list_accounts, list_users, migrate,
    set_hf_path, upsert_user,
)

TEST_DB = "p_music_test"


@contextmanager
def scratch_db():
    """Fresh, empty TEST_DB for the duration of the block, dropped afterwards."""
    admin = connect(dbname="postgres")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        cur.execute(f"CREATE DATABASE {TEST_DB}")
    conn = connect(dbname=TEST_DB)
    try:
        yield conn
    finally:
        conn.close()
        with admin.cursor() as cur:
            cur.execute(f"DROP DATABASE {TEST_DB} WITH (FORCE)")
        admin.close()


def applied(conn):
    with conn, conn.cursor() as cur:
        cur.execute("SELECT name FROM schema_migrations ORDER BY name")
        return [r[0] for r in cur.fetchall()]


def table_exists(conn, name):
    with conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (name,))
        return cur.fetchone()[0]


def test_migrate():
    with tempfile.TemporaryDirectory() as tmp, scratch_db() as conn:
        d = Path(tmp)
        (d / "001_a.sql").write_text("CREATE TABLE a (id int);")
        (d / "002_b.sql").write_text("CREATE TABLE b (id int); SELECT 1/0;")

        # A failing file rolls back on its own; earlier files stay applied.
        try:
            migrate(conn, d)
            raise AssertionError("002_b.sql should have failed")
        except psycopg2.Error:
            pass
        assert applied(conn) == ["001_a.sql"]
        assert table_exists(conn, "a") and not table_exists(conn, "b")

        # Fixed file applies; files run in name order; a second run is a no-op.
        (d / "002_b.sql").write_text("CREATE TABLE b (id int);")
        (d / "010_c.sql").write_text("CREATE TABLE c (b_id int);")
        assert migrate(conn, d) == ["002_b.sql", "010_c.sql"]
        assert migrate(conn, d) == []
        assert applied(conn) == ["001_a.sql", "002_b.sql", "010_c.sql"]


def test_repo_migrations_apply():
    """The real migrations/ folder applies cleanly to an empty DB, twice."""
    with scratch_db() as conn:
        migrate(conn)
        assert migrate(conn) == []


def run(conn, sql, params=()):
    with conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def rejected(conn, sql, params=()):
    """True if the statement violates a constraint (CHECK, UNIQUE, FK); the transaction is rolled back."""
    try:
        run(conn, sql, params)
    except psycopg2.IntegrityError:
        return True
    return False


def test_core_schema():
    with scratch_db() as conn:
        migrate(conn)
        user = "INSERT INTO users (handle, {col}) VALUES (%s, %s)"

        # Valid person with full demographics.
        uid = run(conn, """
            INSERT INTO users (handle, display_name, gender, birth_year, city, country, native_language,
                               languages, musical_background, instruments, music_training_years, extra)
            VALUES ('kien', 'Dylan', 'male', 2001, 'Hanoi', 'VN', 'vi', '{en}', 'self_taught',
                    '{guitar}', 3, '{"income_band": "b"}') RETURNING id""")[0][0]
        assert run(conn, "SELECT languages, instruments, favorite_genres FROM users WHERE id = %s", (uid,)) \
            == [(["en"], ["guitar"], [])]  # array columns default to empty, never NULL

        # Rejections.
        assert rejected(conn, "INSERT INTO users (handle) VALUES ('kien')")               # duplicate handle
        assert rejected(conn, "INSERT INTO users (handle) VALUES ('Bad Handle')")         # handle format
        assert rejected(conn, user.format(col="birth_year"), ("a", 1850))
        assert rejected(conn, user.format(col="birth_year"), ("a", 2200))
        assert rejected(conn, user.format(col="gender"), ("a", "man"))                    # not in fixed list
        assert rejected(conn, user.format(col="musical_background"), ("a", "expert"))
        assert rejected(conn, user.format(col="music_training_years"), ("a", -1))
        assert rejected(conn, user.format(col="country"), ("a", "Vietnam"))               # ISO alpha-2 only
        assert rejected(conn, user.format(col="extra"), ("a", "[1, 2]"))                  # extra must be an object

        # updated_at moves on UPDATE.
        before = run(conn, "SELECT updated_at FROM users WHERE id = %s", (uid,))[0][0]
        run(conn, "UPDATE users SET city = 'Da Nang' WHERE id = %s", (uid,))
        assert run(conn, "SELECT updated_at FROM users WHERE id = %s", (uid,))[0][0] > before

        # One person, two services; a (source, username) pair belongs to one person only.
        acct = "INSERT INTO user_accounts (user_id, source, source_username) VALUES (%s, %s, %s)"
        run(conn, acct, (uid, "spotify", "kien_sp"))
        run(conn, acct, (uid, "apple_music", "kien"))
        other = run(conn, "INSERT INTO users (handle) VALUES ('other') RETURNING id")[0][0]
        assert rejected(conn, acct, (other, "spotify", "kien_sp"))
        assert rejected(conn, acct, (other, "tidal", "x"))
        assert rejected(conn, "INSERT INTO user_accounts (user_id, source, source_username, active_from, active_to)"
                              " VALUES (%s, 'spotify', 'y', '2025-01-01', '2024-01-01')", (other,))
        assert rejected(conn, "DELETE FROM users WHERE id = %s", (uid,))                  # has accounts

        # Tracks, credits, external ids.
        tid = run(conn, "INSERT INTO tracks (match_key, song_key, title) VALUES ('obito|xa xoi', 'obito|xa xoi', 'Xa Xôi') "
                        "RETURNING id")[0][0]
        a1 = run(conn, "INSERT INTO artists (name) VALUES ('Obito') RETURNING id")[0][0]
        a2 = run(conn, "INSERT INTO artists (name) VALUES ('Shiki') RETURNING id")[0][0]
        assert rejected(conn, "INSERT INTO artists (name) VALUES ('OBITO')")              # case-insensitive unique
        assert rejected(conn, "INSERT INTO tracks (match_key, song_key, title) VALUES ('obito|xa xoi', 'obito|xa xoi', 'dup')")
        run(conn, "INSERT INTO tracks (match_key, song_key, title) VALUES ('obito|xa xoi|v:remix', 'obito|xa xoi', 'Xa Xôi (Remix)')")
        run(conn, "INSERT INTO track_artists (track_id, artist_id, position) VALUES (%s, %s, 0), (%s, %s, 1)",
            (tid, a1, tid, a2))
        assert rejected(conn, "INSERT INTO track_artists (track_id, artist_id, position) VALUES (%s, %s, 2)",
                        (tid, a1))                                                        # same artist twice
        ext = "INSERT INTO track_external_ids (source, external_id, track_id) VALUES (%s, %s, %s)"
        run(conn, ext, ("spotify", "spotify:track:1", tid))
        assert rejected(conn, ext, ("spotify", "spotify:track:1", tid))

        # Ingest runs hang off an account.
        aid = run(conn, "SELECT id FROM user_accounts WHERE source_username = 'kien'")[0][0]
        run(conn, "INSERT INTO ingest_runs (account_id, file_hash, row_count) VALUES (%s, 'abc', 10)", (aid,))
        assert rejected(conn, "DELETE FROM user_accounts WHERE id = %s", (aid,))          # has ingest runs


def test_birth_year_from_age():
    assert birth_year_from_age(25, today=date(2026, 9, 25)) == 2001


def test_people_and_accounts():
    with scratch_db() as conn:
        migrate(conn)

        # Create, then a partial edit: given fields change, the rest are kept.
        uid = upsert_user(conn, "kien", display_name="Dylan", gender="male", birth_year=2001,
                          country="VN", native_language="vi", languages=["en"], instruments=["guitar"],
                          extra={"income_band": "b"})
        assert upsert_user(conn, "kien", city="Hanoi", instruments=["guitar", "piano"]) == uid
        u = get_user(conn, "kien")
        assert (u["display_name"], u["city"], u["birth_year"]) == ("Dylan", "Hanoi", 2001)
        assert u["instruments"] == ["guitar", "piano"] and u["extra"] == {"income_band": "b"}
        assert run(conn, "SELECT count(*) FROM users")[0][0] == 1
        assert get_user(conn, "nobody") is None

        # Explicit None clears a field; None for an array field means empty.
        upsert_user(conn, "kien", city=None, languages=None)
        u = get_user(conn, "kien")
        assert u["city"] is None and u["languages"] == []

        # Bad input raises instead of writing.
        for bad in [dict(birth_year=date.today().year + 1), dict(favourite_colour="red")]:
            try:
                upsert_user(conn, "kien", **bad)
                raise AssertionError(f"accepted {bad}")
            except ValueError:
                pass

        # Spotify first, Apple later: two accounts on one person.
        add_account(conn, "kien", "spotify", "kien_sp", active_from=date(2020, 1, 1), active_to=date(2024, 6, 1))
        add_account(conn, "kien", "apple_music", "kien", active_from=date(2024, 6, 1))
        assert [(a["source"], a["source_username"]) for a in list_accounts(conn, "kien")] \
            == [("apple_music", "kien"), ("spotify", "kien_sp")]

        upsert_user(conn, "bhuy")
        try:
            add_account(conn, "bhuy", "spotify", "kien_sp")
            raise AssertionError("took another person's account")
        except psycopg2.errors.UniqueViolation:
            pass
        try:
            add_account(conn, "ghost", "spotify", "x")
            raise AssertionError("account for unknown person")
        except ValueError:
            pass

        assert [u["handle"] for u in list_users(conn)] == ["bhuy", "kien"]


def test_hf_path_ownership():
    with scratch_db() as conn:
        migrate(conn)
        upsert_user(conn, "a")
        upsert_user(conn, "b")
        acct_a = add_account(conn, "a", "apple_music", "a")
        acct_b = add_account(conn, "b", "apple_music", "b")
        run_id = run(conn, "INSERT INTO ingest_runs (account_id, file_hash) VALUES (%s, 'h') RETURNING id", (acct_a,))[0][0]
        assert not account_pushed(conn, acct_a, "raw_am/Library_a.xml")
        set_hf_path(conn, run_id, "raw_am/Library_a.xml")
        assert account_pushed(conn, acct_a, "raw_am/Library_a.xml")
        assert not account_pushed(conn, acct_b, "raw_am/Library_a.xml")


if __name__ == "__main__":
    test_migrate()
    test_repo_migrations_apply()
    test_core_schema()
    test_birth_year_from_age()
    test_people_and_accounts()
    test_hf_path_ownership()
    print("test_db: ok")
