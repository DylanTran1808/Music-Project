"""
common.py

Shared config and repo-browsing helpers for the Streaming-data connectors
(Apple Music, Spotify, audio embeddings). Every connector module imports
from here so the repo id, auth, and path handling stay in one place.
"""

import os
from typing import Optional

from dotenv import load_dotenv
from huggingface_hub import HfFileSystem, hf_hub_download, list_repo_files

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_REPO_ID = "CongtyTuban/Streaming-data"   # adjust if the namespace differs
HF_TOKEN = os.getenv("HF_TOKEN")                  # set this in a .env file;

RAW_AM_DIR = "raw_am"
RAW_SPOT_DIR = "raw_spot"
EMBEDDINGS_DIR = "audio_embeddings"   # convention for the future audio-embedding dataset


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def get_fs(token: Optional[str] = HF_TOKEN) -> HfFileSystem:
    """Returns an HfFileSystem instance for reading hf:// paths."""
    return HfFileSystem(token=token)


def all_repo_files(repo_id: str = DEFAULT_REPO_ID, token: Optional[str] = HF_TOKEN) -> list:
    """Lists every file in the dataset repo."""
    return list_repo_files(repo_id, repo_type="dataset", token=token)


def hf_path(repo_id: str, *parts: str) -> str:
    """
    Builds an hf://datasets/... path from parts, e.g.
        hf_path(repo_id, RAW_AM_DIR, "Library_kien.xml")
        -> "hf://datasets/<repo_id>/raw_am/Library_kien.xml"
    """
    joined = "/".join(p.strip("/") for p in parts if p)
    return f"hf://datasets/{repo_id}/{joined}"


def download_file(path_in_repo: str, repo_id: str = DEFAULT_REPO_ID, token: Optional[str] = HF_TOKEN) -> str:
    """Downloads (and caches locally) one file from the repo, returning the local path."""
    return hf_hub_download(repo_id=repo_id, filename=path_in_repo, repo_type="dataset", token=token)