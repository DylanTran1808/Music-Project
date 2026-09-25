"""
connectors

HF dataset connectors for Project-Music, split by source:
    - apple_music.py       -> raw_am/Library_<username>.xml (plist)
    - spotify.py            -> raw_spot/Spotify_<username>/*.json
    - audio_embeddings.py   -> embeddings/<model_name>/*.parquet (future dataset)
    - common.py              -> shared config (repo id, token, path helpers)
    - itunes_preview.py      -> 30s preview clips via the iTunes Search API

Import from the submodules directly, e.g.:
    from music_project.connectors.apple_music import apple_music_library_to_df
    from music_project.connectors.spotify import spotify_user_to_df

Or use the shortcuts re-exported below.
"""

from .apple_music import (
    apple_music_library_to_df,
    list_apple_music_files,
    load_apple_music_library,
    playlists_to_df,
    stream_apple_music_tracks,
    tracks_to_df,
)
# from .audio_embeddings import (
#     embeddings_to_matrix,
#     list_embedding_files,
#     list_embedding_models,
#     load_embeddings,
#     merge_with_tracks,
#     stream_all_embeddings,
#     stream_embeddings,
# )
from .common import DEFAULT_REPO_ID, HF_TOKEN, download_file
from .itunes_preview import (
    debug_search,
    enrich_library_with_previews,
    get_apple_preview,
    save_and_play_preview,
    save_preview,
)
from .spotify import (
    list_spotify_files,
    list_spotify_users,
    spotify_user_to_df,
    stream_all_spotify_for_user,
    stream_spotify_json,
)

__all__ = [
    # common
    "DEFAULT_REPO_ID", "HF_TOKEN", "download_file",
    # apple music
    "list_apple_music_files", "load_apple_music_library",
    "stream_apple_music_tracks", "apple_music_library_to_df",
    "tracks_to_df", "playlists_to_df",
    # spotify
    "list_spotify_users", "list_spotify_files",
    "stream_spotify_json", "stream_all_spotify_for_user", "spotify_user_to_df",
    # itunes previews
    "get_apple_preview", "debug_search", "save_preview",
    "save_and_play_preview", "enrich_library_with_previews",
    # ------audio embeddings
    # "list_embedding_models", "list_embedding_files",
    # "load_embeddings", "stream_embeddings", "stream_all_embeddings",
    # "embeddings_to_matrix", "merge_with_tracks",
]