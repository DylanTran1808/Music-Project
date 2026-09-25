"""
audio_embeddings.py

Ingestor scaffold for a future audio-embedding dataset in the Streaming-data
(or a dedicated embeddings) HF repo.

Assumed layout (adjust EMBEDDINGS_DIR / filenames once the real dataset
exists — nothing else here depends on the exact naming):

    embeddings/
        <model_name>/
            *.parquet     -> one or more Parquet files, each with at least:
                                 - an id column to join back to track metadata
                                   (default: "track_id")
                                 - an embedding column holding a fixed-length
                                   vector per row (default: "embedding")

Parquet was chosen deliberately over JSON: it's columnar and supports true
batch streaming via pyarrow, so you can iterate over embeddings without
loading a whole file (or the whole dataset) into memory — important once
this dataset is large.

Install once:
    pip install pyarrow --break-system-packages
"""

from typing import Iterator, Optional

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from .common import DEFAULT_REPO_ID, EMBEDDINGS_DIR, HF_TOKEN, all_repo_files, get_fs, hf_path


# ---------------------------------------------------------------------------
# Repo browsing
# ---------------------------------------------------------------------------

def list_embedding_files(
    model_name: Optional[str] = None,
    repo_id: str = DEFAULT_REPO_ID,
    token: Optional[str] = HF_TOKEN,
) -> list:
    """
    List every .parquet file under embeddings/, optionally scoped to one
    model's subfolder (embeddings/<model_name>/).
    """
    files = all_repo_files(repo_id, token)
    prefix = f"{EMBEDDINGS_DIR}/{model_name}/" if model_name else f"{EMBEDDINGS_DIR}/"
    return [f for f in files if f.startswith(prefix) and f.endswith(".parquet")]


def list_embedding_models(repo_id: str = DEFAULT_REPO_ID, token: Optional[str] = HF_TOKEN) -> list:
    """List embedding model subfolder names under embeddings/."""
    files = all_repo_files(repo_id, token)
    models = set()
    prefix = f"{EMBEDDINGS_DIR}/"
    for f in files:
        if f.startswith(prefix) and f.endswith(".parquet"):
            remainder = f[len(prefix):]
            if "/" in remainder:
                models.add(remainder.split("/", 1)[0])
    return sorted(models)


# ---------------------------------------------------------------------------
# Loading / streaming
# ---------------------------------------------------------------------------

def load_embeddings(path_in_repo: str, repo_id: str = DEFAULT_REPO_ID, token: Optional[str] = HF_TOKEN) -> pd.DataFrame:
    """Loads one Parquet embeddings file fully into a DataFrame."""
    fs = get_fs(token)
    path = hf_path(repo_id, path_in_repo)
    with fs.open(path, "rb") as f:
        return pd.read_parquet(f)


def stream_embeddings(
    path_in_repo: str,
    repo_id: str = DEFAULT_REPO_ID,
    token: Optional[str] = HF_TOKEN,
    batch_size: int = 1024,
) -> Iterator[pd.DataFrame]:
    """
    Streams a Parquet embeddings file in row-group batches, yielding a
    DataFrame per batch instead of loading the whole file at once.
    Use this once individual embedding files get large.

    Usage:
        for batch_df in stream_embeddings("embeddings/clap/part-0.parquet"):
            process(batch_df)
    """
    fs = get_fs(token)
    path = hf_path(repo_id, path_in_repo)
    with fs.open(path, "rb") as f:
        parquet_file = pq.ParquetFile(f)
        for batch in parquet_file.iter_batches(batch_size=batch_size):
            yield batch.to_pandas()


def stream_all_embeddings(
    model_name: str,
    repo_id: str = DEFAULT_REPO_ID,
    token: Optional[str] = HF_TOKEN,
    batch_size: int = 1024,
) -> Iterator[pd.DataFrame]:
    """Streams batches across every Parquet file for one embedding model, in turn."""
    for path in list_embedding_files(model_name, repo_id, token):
        yield from stream_embeddings(path, repo_id, token, batch_size=batch_size)


# ---------------------------------------------------------------------------
# Convenience: vectors + joining back to track metadata
# ---------------------------------------------------------------------------

def embeddings_to_matrix(df: pd.DataFrame, embedding_col: str = "embedding") -> np.ndarray:
    """Stacks a DataFrame's embedding column (list/array per row) into a single 2D numpy array."""
    return np.stack(df[embedding_col].to_numpy())


def merge_with_tracks(
    track_df: pd.DataFrame,
    embeddings_df: pd.DataFrame,
    on: str = "track_id",
    how: str = "left",
) -> pd.DataFrame:
    """
    Joins an embeddings DataFrame onto a track-metadata DataFrame (e.g. from
    apple_music_library_to_df) on a shared id column. Adjust `on` if the
    real dataset ends up keyed by persistent_id or something else instead.
    """
    return track_df.merge(embeddings_df, on=on, how=how)