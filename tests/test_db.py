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


if __name__ == "__main__":
    test_migrate()
    test_repo_migrations_apply()
    print("test_db: ok")
