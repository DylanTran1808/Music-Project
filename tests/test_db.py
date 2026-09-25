"""DB checks against a throwaway p_music_test database. Run: uv run python tests/test_db.py"""

import tempfile
from contextlib import contextmanager
from pathlib import Path

import psycopg2

from music_project.db import connect, migrate

TEST_DB = "p_music_test"


@contextmanager
def scratch_db():
    """Fresh, empty TEST_DB for the duration of the block, dropped afterwards."""
    admin = connect(dbname="postgres")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
        cur.execute(f"CREATE DATABASE {TEST_DB}")
    conn = connect(dbname=TEST_DB)
    try:
        yield conn
    finally:
        conn.close()
        with admin.cursor() as cur:
            cur.execute(f"DROP DATABASE {TEST_DB}")
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
        tid = run(conn, "INSERT INTO tracks (match_key, title) VALUES ('obito|xa xoi', 'Xa Xôi') RETURNING id")[0][0]
        a1 = run(conn, "INSERT INTO artists (name) VALUES ('Obito') RETURNING id")[0][0]
        a2 = run(conn, "INSERT INTO artists (name) VALUES ('Shiki') RETURNING id")[0][0]
        assert rejected(conn, "INSERT INTO artists (name) VALUES ('OBITO')")              # case-insensitive unique
        assert rejected(conn, "INSERT INTO tracks (match_key, title) VALUES ('obito|xa xoi', 'dup')")
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


if __name__ == "__main__":
    test_migrate()
    test_repo_migrations_apply()
    test_core_schema()
    print("test_db: ok")
