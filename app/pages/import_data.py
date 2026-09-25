"""Import page: load a person's listening exports into p_music."""

import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import psycopg2
import streamlit as st

from common import UPLOAD_DIR, friendly, get_conn
from music_project import db
from music_project.db.load import load_apple, load_spotify

# Extended (Streaming_History_Audio_2024.json) and legacy (StreamingHistory0.json) history files.
HISTORY_FILE = re.compile(r"^Streaming_?History.*\.json$", re.I)


def unpack_spotify(uploads, dest: Path) -> int:
    """Writes the history JSONs from uploaded .zip/.json files into dest; returns how many."""
    n = 0
    for up in uploads:
        if up.name.lower().endswith(".zip"):
            with zipfile.ZipFile(up) as z:
                members = [(m, z.read(m)) for m in z.namelist() if HISTORY_FILE.match(Path(m).name)]
        else:
            members = [(up.name, up.getvalue())] if HISTORY_FILE.match(up.name) else []
        for name, data in members:
            (dest / Path(name).name).write_bytes(data)  # basename only: "../x.json" can't escape dest
            n += 1
    return n


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

st.subheader("Spotify streaming history")
spotify = [i for i, a in accounts.items() if a["source"] == "spotify"]
if not spotify:
    st.info("This person has no Spotify account yet. Add one on the Users page first.")
else:
    acct = accounts[st.selectbox("Account", spotify, format_func=lambda i: accounts[i]["source_username"],
                                 key="spotify_account")]
    st.caption("Spotify → Account → Privacy settings → request *Extended streaming history*. Upload the "
               ".zip you get, or the Streaming_History_*.json files. Plays already stored are skipped, "
               "so uploading overlapping exports is safe.")
    uploads = st.file_uploader("Export (.zip or .json)", type=["zip", "json"], accept_multiple_files=True,
                               key="spotify_files")
    if st.button("Load history", key="load_spotify", disabled=not uploads):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                if not unpack_spotify(uploads, Path(tmp)):
                    raise ValueError("no Streaming_History*.json files in the upload")
                with st.spinner("Loading…"):
                    result = load_spotify(conn, acct["id"], tmp)
            except Exception as err:  # user-supplied files: show any parse/load failure, keep the old data
                st.error(friendly(err) if isinstance(err, psycopg2.Error)
                         else f"Could not load this Spotify export: {err}")
            else:
                target = UPLOAD_DIR / "spotify" / f"Spotify_{acct['source_username']}"
                target.mkdir(parents=True, exist_ok=True)
                for f in Path(tmp).glob("*.json"):
                    shutil.copy2(f, target / f.name)
                st.success(f"Loaded {result['events']} plays ({result['new_events']} new) "
                           f"for {acct['source_username']}.")
