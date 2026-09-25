"""
Music-Project intake app: people, their demographics, service accounts and listening exports.

Run: uv run streamlit run app/streamlit_app.py   (localhost only; no auth)
Needs DATABASE_URL in .env and an up-to-date schema (uv run python -m music_project.db migrate).
"""

import streamlit as st

st.set_page_config(page_title="Music-Project intake", layout="wide")
st.navigation([
    st.Page("pages/users.py", title="Users", url_path="users", default=True),
    st.Page("pages/import_data.py", title="Import", url_path="import"),
]).run()
