"""
Postgres connector for the p_music database.

connect() reads DATABASE_URL from .env (libpq URL, e.g. postgresql:///p_music).
migrate() applies migrations/NNN_name.sql in name order, one transaction per
file, and records each applied file in schema_migrations. Migration files must
not contain their own BEGIN/COMMIT, or a failure can't roll the file back.
"""

import os
from pathlib import Path
from typing import Optional

import psycopg2
from dotenv import load_dotenv
from psycopg2.extensions import make_dsn

load_dotenv()

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"


def connect(url: Optional[str] = None, **overrides):
    """New connection to DATABASE_URL (or `url`); keyword overrides such as dbname= win."""
    url = url or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set; add e.g. DATABASE_URL=postgresql:///p_music to .env")
    return psycopg2.connect(make_dsn(url, **overrides))


def migrate(conn, migrations_dir: Path = MIGRATIONS_DIR) -> list:
    """Applies pending .sql files in name order; returns the names applied. Stops at the first failure."""
    with conn, conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                           name text PRIMARY KEY,
                           applied_at timestamptz NOT NULL DEFAULT now())""")
        cur.execute("SELECT name FROM schema_migrations")
        done = {r[0] for r in cur.fetchall()}

    applied = []
    for path in sorted(Path(migrations_dir).glob("*.sql")):
        if path.name in done:
            continue
        with conn, conn.cursor() as cur:  # commits on success, rolls back this file on error
            cur.execute(path.read_text(encoding="utf-8"))
            cur.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
        applied.append(path.name)
    return applied
