"""
Postgres connector for the p_music database.

connect() reads DATABASE_URL from .env (libpq URL, e.g. postgresql:///p_music).
migrate() applies migrations/NNN_name.sql in name order, one transaction per
file, and records each applied file in schema_migrations. Migration files must
not contain their own BEGIN/COMMIT, or a failure can't roll the file back.
"""

import os
from datetime import date
from pathlib import Path
from typing import Optional

import psycopg2
from dotenv import load_dotenv
from psycopg2 import sql
from psycopg2.extensions import make_dsn
from psycopg2.extras import Json, RealDictCursor

load_dotenv()

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"

# Editable columns of `users` (everything except id/handle/timestamps). Allowed values
# for the fixed lists live in the CHECK constraints of migrations/001_core.sql.
USER_FIELDS = (
    "display_name", "gender", "birth_year", "city", "country", "timezone", "occupation",
    "education_level", "native_language", "languages", "musical_background", "instruments",
    "music_training_years", "favorite_genres", "notes", "extra",
)
ARRAY_FIELDS = {"languages", "instruments", "favorite_genres"}
GENDERS = ("female", "male", "non_binary", "other", "prefer_not_to_say")
MUSICAL_BACKGROUNDS = ("none", "listener_only", "self_taught", "formal_training", "professional")
SOURCES = ("apple_music", "spotify")


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


# ---------------------------------------------------------------------------
# People + accounts
# ---------------------------------------------------------------------------

def birth_year_from_age(age: int, today: Optional[date] = None) -> int:
    """Birth year for someone `age` years old today (may be off by one before their birthday)."""
    return (today or date.today()).year - age


def upsert_user(conn, handle: str, **fields) -> int:
    """
    Creates the person or updates only the given fields; returns users.id.
    None clears a field (an empty list for array fields). Values the schema rejects
    raise psycopg2.IntegrityError; unknown fields or a future birth_year raise ValueError.
    """
    unknown = set(fields) - set(USER_FIELDS)
    if unknown:
        raise ValueError(f"unknown user fields: {sorted(unknown)}")
    if (fields.get("birth_year") or 0) > date.today().year:
        raise ValueError("birth_year is in the future")
    for f in ARRAY_FIELDS & fields.keys():
        fields[f] = fields[f] or []
    if "extra" in fields:
        fields["extra"] = Json(fields["extra"] or {})

    cols = ["handle", *fields]
    updates = [sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(c)) for c in fields] \
        or [sql.SQL("handle = EXCLUDED.handle")]  # no-op update so RETURNING still yields the id
    query = sql.SQL("INSERT INTO users ({}) VALUES ({}) ON CONFLICT (handle) DO UPDATE SET {} RETURNING id").format(
        sql.SQL(", ").join(map(sql.Identifier, cols)),
        sql.SQL(", ").join(sql.Placeholder() * len(cols)),
        sql.SQL(", ").join(updates),
    )
    with conn, conn.cursor() as cur:
        cur.execute(query, [handle, *fields.values()])
        return cur.fetchone()[0]


def get_user(conn, handle: str) -> Optional[dict]:
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM users WHERE handle = %s", (handle,))
        return cur.fetchone()


def list_users(conn) -> list:
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT id, handle, display_name FROM users ORDER BY handle")
        return cur.fetchall()


def add_account(conn, handle: str, source: str, source_username: str,
                active_from: Optional[date] = None, active_to: Optional[date] = None) -> int:
    """Links a service account to a person; a (source, username) already owned by anyone raises UniqueViolation."""
    with conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO user_accounts (user_id, source, source_username, active_from, active_to)
            SELECT id, %s, %s, %s, %s FROM users WHERE handle = %s
            RETURNING id""", (source, source_username, active_from, active_to, handle))
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"no user with handle {handle!r}")
    return row[0]


def list_accounts(conn, handle: str) -> list:
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT a.id, a.source, a.source_username, a.active_from, a.active_to
            FROM user_accounts a JOIN users u ON u.id = a.user_id
            WHERE u.handle = %s ORDER BY a.source, a.source_username""", (handle,))
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Hugging Face bookkeeping
# ---------------------------------------------------------------------------

def set_hf_path(conn, run_id: int, hf_path: str) -> None:
    """Records that an ingest run's export is on Hugging Face at hf_path."""
    with conn, conn.cursor() as cur:
        cur.execute("UPDATE ingest_runs SET hf_path = %s WHERE id = %s", (hf_path, run_id))


def account_pushed(conn, account_id: int, hf_path: str) -> bool:
    """True if this account has put hf_path on Hugging Face before, i.e. it may overwrite it."""
    with conn, conn.cursor() as cur:
        cur.execute("SELECT EXISTS (SELECT 1 FROM ingest_runs WHERE account_id = %s AND hf_path = %s)",
                    (account_id, hf_path))
        return cur.fetchone()[0]
