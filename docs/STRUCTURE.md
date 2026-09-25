# Project Structure

Current layout of `Music-Project` (branch `basic_build`). Data comes from the Hugging Face dataset
`CongtyTuban/Streaming-data`; nothing large is committed to git.

## Directory tree

```
Music-Project/
├── pyproject.toml            # deps + uv config (torch CPU on macOS, CUDA 12.1 on Windows)
├── uv.lock                   # pinned versions, do not edit by hand
├── .python-version
├── README.md                 # setup guide (uv)
│
├── src/music_project/
│   ├── __init__.py
│   ├── connectors/           # read raw data from the HF dataset repo
│   │   ├── __init__.py       # re-exports the public connector API
│   │   ├── common.py         # repo id, HF_TOKEN, hf:// path + file helpers
│   │   ├── apple_music.py    # raw_am/Library_<user>.xml  -> DataFrame
│   │   ├── spotify.py        # raw_spot/Spotify_<user>/*.json -> DataFrame
│   │   ├── itunes_preview.py # iTunes Search API -> 30s .m4a preview clips
│   │   └── audio_embeddings.py  # Parquet embeddings (scaffold, not exported yet)
│   └── analysis/
│       ├── apple_insights.py # Apple XML -> recsys track features + taste profile / CLI
│       ├── spotify_insights.py # Spotify history -> per-play events, track features, profile / CLI
│       └── report.py         # both profiles + cross-source overlap, build_report()
│
├── scripts/
│   └── am_previews_fetch.py  # CSV of songs -> 30s .m4a previews via iTunes Search API
│
├── notebooks/                # exploration only
│   ├── parse.ipynb           # early AM/Spotify parsing (logic now in connectors/)
│   ├── streamdata.ipynb      # early HF dataset streaming test
│   ├── sampleAnalytics.ipynb # build_report() usage
│   └── sampleFetch.ipynb     # iTunes search + loading all users' data
│
├── tests/
│   ├── test_connectors.py    # smoke script: build_report("kha")
│   ├── test_apple_insights.py # offline asserts on a synthetic library
│   ├── test_spotify_insights.py # offline asserts on a synthetic history
│   └── apple_fetch.py        # preview -> waveform -> MERT / CLAP embedding experiments
│
└── data/                     # local cache, gitignored (except .gitkeep)
    ├── raw_am/Library_<user>.xml
    ├── raw_spot/Spotify_<user>/
    └── temp_vi_en_songs.csv  # input for am_previews_fetch.py
```

## Module dependencies

```mermaid
graph TD
    subgraph HF["Hugging Face: CongtyTuban/Streaming-data"]
        AMX["raw_am/Library_#lt;user#gt;.xml"]
        SPJ["raw_spot/Spotify_#lt;user#gt;/*.json"]
        EMB["audio_embeddings/#lt;model#gt;/*.parquet<br/>(future)"]
    end

    subgraph connectors["music_project.connectors"]
        common["common.py<br/>DEFAULT_REPO_ID, HF_TOKEN,<br/>get_fs, hf_path, all_repo_files"]
        am["apple_music.py"]
        sp["spotify.py"]
        ae["audio_embeddings.py"]
        init["__init__.py<br/>(re-exports)"]
    end

    ai["analysis/apple_insights.py"]
    si["analysis/spotify_insights.py"]
    report["analysis/report.py<br/>build_report()"]
    nb["notebooks/*.ipynb"]
    tc["tests/test_connectors.py"]
    env[".env (HF_TOKEN)"]

    env --> common
    common --> am
    common --> sp
    common --> ae
    am --> init
    sp --> init
    common --> init
    AMX --> am
    SPJ --> sp
    EMB -.-> ae
    am --> ai
    sp --> si
    ai -->|"text + stats helpers"| si
    ai --> report
    si --> report
    report --> nb
    report --> tc
    init --> nb
```

## Data flow: `build_report(username)`

```mermaid
flowchart LR
    U([username]) --> chkA{"Library_#lt;user#gt;.xml<br/>exists?"}
    U --> chkS{"Spotify_#lt;user#gt;/<br/>exists?"}

    chkA -- yes --> lib["load_library<br/>tracks + playlists, one download"]
    chkS -- yes --> hist["load_history<br/>events (ip_addr dropped), local time, sessions"]

    lib --> amT["lib.tracks<br/>artists, language, versions, freshness,<br/>playlist tags, affinity"]
    hist --> spT["hist.tracks<br/>streams, skips, replays, chosen,<br/>est. duration, affinity"]

    amT --> amP["apple profile<br/>affinity tables, drift, segments,<br/>playlists, artist co-occurrence"]
    spT --> spP["spotify profile<br/>behaviour, time of day, sessions,<br/>discovery, segments, transitions"]

    amT --> ov["source_overlap<br/>artists + track_key"]
    spT --> ov

    amP --> R[["report dict<br/>apple / spotify / overlap"]]
    spP --> R
    ov --> R

    chkA -- no --> R
    chkS -- no --> R
```

A missing source comes back as `None`; overlap is only computed when both exist.

## Audio preview / embedding pipeline (experimental)

```mermaid
flowchart LR
    csv["data/temp_vi_en_songs.csv<br/>(Name, Artist)"] --> search["iTunes Search API<br/>storefronts: us → vn"]
    search --> score{"fuzzy match<br/>0.7·title + 0.3·artist<br/>≥ 0.5?"}
    score -- no --> skip["log + skip"]
    score -- yes --> m4a["previews/*.m4a"]
    m4a --> wave["pydub + ffmpeg<br/>mono waveform"]
    wave -->|24 kHz| mert["MERT-v1-330M<br/>13 layer vectors"]
    wave -->|48 kHz| clap["CLAP htsat-unfused<br/>512-d vector"]
    mert -.-> parquet["audio_embeddings/*.parquet<br/>(planned)"]
    clap -.-> parquet
```

`scripts/am_previews_fetch.py` covers CSV → `.m4a`. Waveform and embedding steps live only in
`tests/apple_fetch.py` for now.

## Known loose ends

- `analysis/` has no `__init__.py`. Imports still work because it's a namespace package.
- `audio_embeddings.py` docstring says `embeddings/`, but `EMBEDDINGS_DIR` in `common.py` is `audio_embeddings`.
- `tests/` has no real pytest tests. Both files are scripts that make network calls.
- The scripts import `requests`, but it's only installed because another package depends on it. It isn't declared in `pyproject.toml`.
- README §6 still shows the old planned layout (`ingest/`, `db/`, `rag/`…).
