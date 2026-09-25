"""Import page: load a person's listening exports into p_music."""

import os
import tempfile

import psycopg2
import streamlit as st

from common import UPLOAD_DIR, friendly, get_conn
from music_project import db
from music_project.db.load import load_apple

conn = get_conn()
st.title("Import listening data")
handles = [u["handle"] for u in db.list_users(conn)]
if not handles:
    st.info("Create a person on the Users page first.")
    st.stop()
handle = st.selectbox("Person", handles, key="import_person")
accounts = {a["id"]: a for a in db.list_accounts(conn, handle)}

st.subheader("Apple Music library")
apple = [i for i, a in accounts.items() if a["source"] == "apple_music"]
if not apple:
    st.info("This person has no Apple Music account yet. Add one on the Users page first.")
else:
    acct = accounts[st.selectbox("Account", apple, format_func=lambda i: accounts[i]["source_username"],
                                 key="apple_account")]
    st.caption("In the Music app: File → Library → Export Library…, then upload the .xml file.")
    upload = st.file_uploader("Library.xml", type=["xml"], key="apple_file")
    if st.button("Load library", key="load_apple", disabled=upload is None):
        target = UPLOAD_DIR / "apple_music" / f"Library_{acct['source_username']}.xml"
        target.parent.mkdir(parents=True, exist_ok=True)
        # Load from a temp copy; only a file that loaded cleanly replaces the staged one.
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".xml", delete=False) as tmp:
            tmp.write(upload.getvalue())
        try:
            with st.spinner("Loading…"):
                result = load_apple(conn, acct["id"], tmp.name)
        except Exception as err:  # user-supplied file: show any parse/load failure, keep the old data
            os.unlink(tmp.name)
            st.error(friendly(err) if isinstance(err, psycopg2.Error)
                     else f"Could not load this file as an Apple Music library: {err}")
        else:
            os.replace(tmp.name, target)
            st.success(f"Loaded {result['items']} items, {result['plays']} plays for {acct['source_username']}.")
