"""
upload.py

Pushes exports uploaded through the app to the HF dataset, in the layout the
readers in apple_music.py / spotify.py expect:
    raw_am/Library_<username>.xml
    raw_spot/Spotify_<username>/Streaming_History_*.json

Never overwrites a path silently: if it already exists the caller has to pass
overwrite=True, which the app only does for the account that pushed it before.
Spotify files are pushed with the PII fields (ip_addr, ...) removed; the local
staged copies are left as they are. Demographics never go through here.
"""

import json
import tempfile
from pathlib import Path
from typing import Optional

from huggingface_hub import HfApi
from huggingface_hub.errors import HfHubHTTPError

from music_project.analysis.spotify_insights import _PII
from music_project.connectors.common import DEFAULT_REPO_ID, HF_TOKEN, RAW_AM_DIR, RAW_SPOT_DIR


class PushError(Exception):
    """The push failed (network, auth, HF error)."""


class PushRefused(PushError):
    """The target path already exists and overwrite wasn't allowed."""


def _api() -> HfApi:
    return HfApi(token=HF_TOKEN)  # tests replace this with a fake


def apple_path(username: str) -> str:
    return f"{RAW_AM_DIR}/Library_{username}.xml"


def spotify_path(username: str) -> str:
    return f"{RAW_SPOT_DIR}/Spotify_{username}"


def _push(api, path: str, overwrite: bool, do_upload) -> str:
    try:
        files = api.list_repo_files(DEFAULT_REPO_ID, repo_type="dataset")
        exists = any(f == path or f.startswith(path + "/") for f in files)
        if exists and not overwrite:
            raise PushRefused(f"{path} already exists on Hugging Face and wasn't uploaded by this account; "
                              "use a different account username, or ask whoever owns it")
        do_upload()
    except HfHubHTTPError as err:
        status = getattr(err.response, "status_code", None)
        if status in (401, 403):
            raise PushError(f"the Hugging Face token needs write access on {DEFAULT_REPO_ID} "
                            f"(HF answered {status}); put a new token in .env as HF_TOKEN") from err
        raise PushError(f"Hugging Face push failed: {err}") from err
    return path


def push_apple(xml_path, username: str, overwrite: bool = False, api: Optional[HfApi] = None) -> str:
    """Pushes a Library.xml unchanged; returns its path in the dataset."""
    api = api or _api()
    path = apple_path(username)
    return _push(api, path, overwrite, lambda: api.upload_file(
        path_or_fileobj=str(xml_path), path_in_repo=path, repo_id=DEFAULT_REPO_ID, repo_type="dataset",
        commit_message=f"Apple Music library for {username} (via intake app)"))


def push_spotify(folder, username: str, overwrite: bool = False, api: Optional[HfApi] = None) -> str:
    """Pushes a folder of Streaming_History_*.json with PII fields removed; returns the folder's path."""
    api = api or _api()
    path = spotify_path(username)
    with tempfile.TemporaryDirectory() as tmp:
        for f in sorted(Path(folder).glob("*.json")):
            records = json.loads(f.read_text(encoding="utf-8"))
            clean = [{k: v for k, v in r.items() if k not in _PII} for r in records]
            (Path(tmp) / f.name).write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
        return _push(api, path, overwrite, lambda: api.upload_folder(
            folder_path=tmp, path_in_repo=path, repo_id=DEFAULT_REPO_ID, repo_type="dataset",
            commit_message=f"Spotify streaming history for {username} (via intake app)"))
