"""Helpers shared by the app's pages."""

import os
from pathlib import Path

import psycopg2
import streamlit as st

from music_project import db

NEW = "+ New person"
# Uploaded exports are staged here (gitignored), laid out like the HF dataset.
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR") or Path(__file__).resolve().parents[1] / "data" / "uploads")


def _alive(conn) -> bool:
    """Round-trip ping: psycopg2 only notices a dropped connection when a query fails."""
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
        return True
    except psycopg2.Error:
        return False


@st.cache_resource(validate=_alive)  # reconnects after a Postgres restart
def get_conn():
    return db.connect()


def label(value):
    return "—" if value is None else value.replace("_", " ")


def friendly(err):
    """One-line message for errors a user can cause from a form."""
    if isinstance(err, psycopg2.errors.UniqueViolation) and "user_accounts" in (err.diag.constraint_name or ""):
        return "That account username is already linked to someone. Each account belongs to one person."
    if isinstance(err, psycopg2.errors.CheckViolation) and err.diag.constraint_name == "users_handle_check":
        return "Handle must be lowercase letters, digits or _ (e.g. kien_tran)."
    if isinstance(err, psycopg2.Error):
        return f"Not saved: {err.diag.message_primary}"
    return f"Not saved: {err}"


def pick(label_text, options, current, key, names=None):
    """Selectbox with an empty choice first; `names` maps codes to display names."""
    opts = [None, *options]
    fmt = (lambda v: "—" if v is None else f"{names[v]} ({v})") if names else label
    return st.selectbox(label_text, opts, index=opts.index(current) if current in opts else 0,
                        format_func=fmt, key=key)
