"""
Music-Project intake app: people, their demographics and service accounts.

Run: uv run streamlit run app/streamlit_app.py   (localhost only; no auth)
Needs DATABASE_URL in .env and an up-to-date schema (uv run python -m music_project.db migrate).
"""

from datetime import date
from zoneinfo import available_timezones

import pandas as pd
import psycopg2
import pycountry
import streamlit as st

from music_project import db

NEW = "+ New person"
COUNTRIES = {c.alpha_2: c.name for c in sorted(pycountry.countries, key=lambda c: c.name)}
LANGUAGES = {l.alpha_2: l.name for l in sorted(pycountry.languages, key=lambda l: l.name) if hasattr(l, "alpha_2")}
TIMEZONES = sorted(available_timezones())
# Suggestions only: both multiselects accept new entries.
INSTRUMENTS = ["vocals", "piano", "keyboard", "guitar", "bass", "drums", "violin", "cello", "flute",
               "saxophone", "trumpet", "production / DAW", "DJ"]
GENRES = ["pop", "v-pop", "k-pop", "c-pop", "j-pop", "hip hop", "rap", "r&b", "rock", "indie", "edm",
          "ballad", "bolero", "jazz", "classical", "lo-fi", "acoustic", "folk", "metal", "country"]


@st.cache_resource
def get_conn():
    return db.connect()


def label(value):
    return "—" if value is None else value.replace("_", " ")


def friendly(err):
    """One-line message for errors a user can cause from the form."""
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


def user_form(conn, handle):
    """Create (handle=None) or edit a person. Every field is optional except the handle."""
    u = (db.get_user(conn, handle) if handle else None) or {}
    k = handle or "new"  # per-person widget keys, so switching person reloads the values
    with st.form(f"user_form_{k}"):
        c1, c2 = st.columns(2)
        new_handle = c1.text_input("Handle (lowercase, used in URLs and file names)", key=f"handle_{k}") \
            if not handle else c1.text_input("Handle", handle, disabled=True)
        display_name = c2.text_input("Display name", u.get("display_name") or "", key=f"display_name_{k}")

        st.subheader("Demographics")
        c1, c2, c3 = st.columns(3)
        gender = pick("Gender", db.GENDERS, u.get("gender"), f"gender_{k}")
        birth_year = c2.number_input("Birth year", 1900, date.today().year, u.get("birth_year"), step=1,
                                     key=f"birth_year_{k}")
        age = c3.number_input("…or age (used if birth year is empty)", 0, 120, None, step=1, key=f"age_{k}")
        c1, c2, c3 = st.columns(3)
        city = c1.text_input("City", u.get("city") or "", key=f"city_{k}")
        with c2:
            country = pick("Country", list(COUNTRIES), u.get("country"), f"country_{k}", COUNTRIES)
        with c3:
            timezone = pick("Timezone", TIMEZONES, u.get("timezone"), f"timezone_{k}")
        c1, c2 = st.columns(2)
        occupation = c1.text_input("Occupation", u.get("occupation") or "", key=f"occupation_{k}")
        education = c2.text_input("Education level", u.get("education_level") or "", key=f"education_{k}")
        c1, c2 = st.columns(2)
        with c1:
            native = pick("Native language", list(LANGUAGES), u.get("native_language"), f"native_language_{k}",
                          LANGUAGES)
        languages = c2.multiselect("Other languages spoken", list(LANGUAGES), u.get("languages") or [],
                                   format_func=lambda v: f"{LANGUAGES[v]} ({v})", key=f"languages_{k}")

        st.subheader("Musical background")
        c1, c2, c3 = st.columns(3)
        with c1:
            background = pick("Level", db.MUSICAL_BACKGROUNDS, u.get("musical_background"),
                              f"musical_background_{k}")
        current = u.get("instruments") or []
        instruments = c2.multiselect("Instruments", sorted({*INSTRUMENTS, *current}), current,
                                     accept_new_options=True, key=f"instruments_{k}")
        years = c3.number_input("Years of training", 0, 100, u.get("music_training_years"), step=1,
                                key=f"training_years_{k}")

        st.subheader("Taste and notes")
        current = u.get("favorite_genres") or []
        genres = st.multiselect("Favourite genres", sorted({*GENRES, *current}), current,
                                accept_new_options=True, key=f"genres_{k}")
        notes = st.text_area("Notes", u.get("notes") or "", key=f"notes_{k}")
        st.caption("Other demographic fields (key → value)")
        extra = st.data_editor(pd.DataFrame({"key": list((u.get("extra") or {}).keys()),
                                             "value": [str(v) for v in (u.get("extra") or {}).values()]},
                                            dtype="string"),
                               num_rows="dynamic", width="stretch", key=f"extra_{k}")

        if not st.form_submit_button("Save", key=f"save_{k}"):
            return
    target = handle or (new_handle or "").strip()
    if not target:
        st.error("Handle is required.")
        return
    if birth_year is None and age is not None:
        birth_year = db.birth_year_from_age(int(age))
    fields = dict(
        display_name=display_name or None, gender=gender, birth_year=birth_year, city=city or None,
        country=country, timezone=timezone, occupation=occupation or None, education_level=education or None,
        native_language=native, languages=languages, musical_background=background, instruments=instruments,
        music_training_years=years, favorite_genres=genres, notes=notes or None,
        extra={r.key.strip(): r.value for r in extra.dropna(subset=["key"]).itertuples() if r.key.strip()},
    )
    try:
        db.upsert_user(conn, target, **fields)
    except (ValueError, psycopg2.Error) as err:
        st.error(friendly(err))
        return
    st.session_state["select_person"] = target  # applied before the selectbox on the rerun
    st.session_state["flash"] = f"Saved {target}."
    st.rerun()


def accounts(conn, handle):
    st.subheader("Service accounts")
    rows = db.list_accounts(conn, handle)
    if rows:
        st.dataframe(pd.DataFrame(rows).drop(columns="id"), hide_index=True, width="stretch")
    else:
        st.caption("No accounts yet.")
    with st.form(f"account_form_{handle}", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns(4)
        source = c1.selectbox("Service", db.SOURCES, format_func=label, key=f"acct_source_{handle}")
        username = c2.text_input("Username in the export / HF files", key=f"acct_username_{handle}")
        active_from = c3.date_input("Active from", None, key=f"acct_from_{handle}")
        active_to = c4.date_input("Active to", None, key=f"acct_to_{handle}")
        if not st.form_submit_button("Add account", key=f"add_account_{handle}"):
            return
    if not username.strip():
        st.error("Username is required.")
        return
    try:
        db.add_account(conn, handle, source, username.strip(), active_from, active_to)
    except (ValueError, psycopg2.Error) as err:
        st.error(friendly(err))
        return
    st.session_state["flash"] = f"Added {label(source)} account {username.strip()}."
    st.rerun()


def users_page():
    conn = get_conn()
    if "select_person" in st.session_state:
        st.session_state["person"] = st.session_state.pop("select_person")
    if "flash" in st.session_state:
        st.success(st.session_state.pop("flash"))
    handles = [u["handle"] for u in db.list_users(conn)]
    person = st.selectbox("Person", [NEW, *handles], key="person")
    handle = None if person == NEW else person
    user_form(conn, handle)
    if handle:
        accounts(conn, handle)


st.set_page_config(page_title="Music-Project intake", layout="wide")
st.navigation([st.Page(users_page, title="Users", url_path="users")]).run()
