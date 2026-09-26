"""
apple_insights.py

Everything the Apple Music library XML can tell a recommender, in two layers:

1. `lib.tracks`: one row per track with recsys-ready features (split artist
   credits, language/script, version tags like remix/slowed, freshness,
   recency, playlist tags) and an implicit-feedback score (`affinity`).
   This table feeds a model directly: to_parquet it, pivot it, embed it.
2. `build_profile(lib)`: the taste profile built from those features
   (affinity per artist/genre/era/language/version, concentration, drift,
   re-surfacing candidates, playlist structure, artist co-occurrence).

What the export can and can't tell you:
- play_count / skip_count are lifetime totals. play_date_utc / skip_date
  are only the *last* event. There is no per-play history, so time-of-day
  and session patterns aren't recoverable from this file.
- rating is effectively never set. Loved/favorited is the explicit signal.
- The export's own "Date" is used as "now", so recency features don't
  change depending on when you run the analysis.

Usage:
    from music_project.analysis.apple_insights import load_library, build_profile, print_profile

    lib = load_library("kien")                                   # from the HF repo
    lib = load_library("kien", path="data/raw_am/Library_kien.xml")  # offline
    print_profile(build_profile(lib))

    uv run python -m music_project.analysis.apple_insights --user kien kha --local-dir data/raw_am
"""

import argparse
import math
import plistlib
import re
import unicodedata
from collections import Counter
from itertools import combinations
from pathlib import Path
from typing import NamedTuple, Optional

import numpy as np
import pandas as pd

from music_project.connectors.apple_music import load_apple_music_library, playlists_to_df, tracks_to_df

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# Implicit-feedback weights. ponytail: hand-tuned; fit them (e.g. predict
# held-out playlist adds) once there's a model to evaluate against.
W_PLAY = 1.0
W_SKIP = 0.7
W_LOVED = 1.5
W_PLAYLIST = 0.5
RECENCY_HALF_LIFE_DAYS = 180

# A "playlist" covering more than this share of the library is a bulk
# dump (e.g. a migration "Transfer" list), not a taste signal.
BULK_PLAYLIST_SHARE = 0.5
BULK_PLAYLIST_MIN_TRACKS = 200

# A day on which this many tracks were added is a library import/migration,
# so date_added there is "when I imported", not "when I discovered it".
BULK_IMPORT_MIN_TRACKS = 50
BULK_IMPORT_SHARE = 0.2

RECENT_DAYS = 90
FORGOTTEN_AFTER_DAYS = 180

_LIST_COLS = {"artists", "version_tags", "playlist_names"}


class AppleLibrary(NamedTuple):
    user: str
    tracks: pd.DataFrame      # enriched, one row per track
    playlists: pd.DataFrame   # long form, one row per (playlist, track)
    snapshot: pd.Timestamp    # export time, used as "now"


# ---------------------------------------------------------------------------
# Text parsing: artists, versions, language
# ---------------------------------------------------------------------------

# " x " is case-sensitive on purpose so "Lil Nas X" survives.
# ponytail: splits band names containing "&" ("Simon & Garfunkel");
# add an exception list if that starts to matter.
_ARTIST_SEP = re.compile(r"\s*(?:,|&|\s+x\s+|(?i:\bfeat\.?|\bft\.|\bfeaturing\b))\s*")
_FEAT_IN_TITLE = re.compile(r"[(\[]\s*(?:feat\.?|ft\.|featuring|with)\s+([^)\]]+)[)\]]", re.I)
_DECORATION = re.compile(r"[(\[]([^)\]]*)[)\]]|\s-\s(.*)$")

_VERSION_TAGS = {
    # "Extended Mix" / "Original Mix" are not remixes (EDM naming for the long / original cut).
    "remix": r"remix|rmx|(?<!extended )(?<!original )\bmix\b|bootleg|\bflip\b",
    "live": r"\blive\b",
    "acoustic": r"acoustic|unplugged",
    "slowed": r"slowed",
    "sped_up": r"sped\s*up|speed\s*up|nightcore",
    "reverb": r"reverb",
    "instrumental": r"instrumental|karaoke|\binst\b",
    "remaster": r"remaster",
    "cover": r"\bcover\b",
    "lofi": r"lo-?fi",
    "ost": r"\bost\b|soundtrack|\bfrom\s",
    "extended": r"extended",
    "edit": r"\bedit\b",  # radio / movie edit; "edition" doesn't match
}


def split_artists(artist, name) -> list:
    """All credited artists: the artist field split on &/,/feat./x, plus any "(feat. X)" in the title."""
    parts = _ARTIST_SEP.split(artist) if isinstance(artist, str) else []
    if isinstance(name, str):
        for m in _FEAT_IN_TITLE.findall(name):
            parts += _ARTIST_SEP.split(m)
    seen = {}
    for p in parts:
        p = p.strip()
        if p and p.casefold() not in seen:
            seen[p.casefold()] = p
    return list(seen.values())


def canonical_artists(artist_lists: pd.Series) -> pd.Series:
    """Collapses case variants ("HaiSam" / "Haisam") to the most common spelling across the whole table."""
    counts = Counter(a for lst in artist_lists for a in lst)
    best = {}
    for name, n in counts.most_common():
        best.setdefault(name.casefold(), name)
    return artist_lists.map(lambda lst: [best[a.casefold()] for a in lst])


def version_tags(name) -> list:
    """Tags found in a title's (...)/[...]/" - ..." decorations, e.g. "Song (Slowed + Reverb)" -> [slowed, reverb]."""
    if not isinstance(name, str):
        return []
    deco = " ".join(a or b for a, b in _DECORATION.findall(name)).lower()
    return [tag for tag, pat in _VERSION_TAGS.items() if re.search(pat, deco)]


def base_title(name) -> str:
    """Title without decorations, casefolded: the version-independent song identity."""
    if not isinstance(name, str):
        return ""
    s = _DECORATION.sub("", unicodedata.normalize("NFKC", name))
    return " ".join(s.casefold().split())


_VI_MARKS = {"\u031b", "\u0309", "\u0323", "\u0306"}  # horn, hook above, dot below, breve


def script_lang(text) -> str:
    """
    Language guess from the writing system: ko / ja / zh / vi / latin / other.
    Deterministic and instant, unlike langdetect on 3-word titles.
    ponytail: kanji-only Japanese reads as zh, and unaccented Vietnamese
    ("MAY KIN THANH DO") reads as latin; use a real detector on lyrics later.
    """
    if not isinstance(text, str):
        return "other"
    has = lambda lo, hi: any(lo <= ord(c) <= hi for c in text)
    if has(0xAC00, 0xD7AF) or has(0x1100, 0x11FF) or has(0x3130, 0x318F):
        return "ko"
    if has(0x3040, 0x30FF):
        return "ja"
    if has(0x4E00, 0x9FFF):
        return "zh"
    for ch in text:
        if ch in "đĐ":
            return "vi"
        marks = [m for m in unicodedata.normalize("NFD", ch)[1:]]
        if len(marks) >= 2 or _VI_MARKS & set(marks):
            return "vi"
    return "latin" if any(c.isalpha() for c in text) else "other"


# ---------------------------------------------------------------------------
# Loading + enrichment
# ---------------------------------------------------------------------------

def load_library(username: str, path: Optional[str] = None) -> AppleLibrary:
    """Load one user's library from the HF repo, or from a local XML if `path` is given."""
    if path:
        with open(path, "rb") as f:
            raw = plistlib.load(f)
    else:
        raw = load_apple_music_library(username)
    snapshot = pd.Timestamp(raw.get("Date") or pd.Timestamp.now("UTC"))
    snapshot = snapshot.tz_localize("UTC") if snapshot.tzinfo is None else snapshot
    playlists = playlists_to_df(raw)
    tracks = enrich_tracks(tracks_to_df(raw, username), playlists, snapshot)
    return AppleLibrary(username, tracks, playlists, snapshot)


def _curated(playlists: pd.DataFrame, n_tracks: int) -> tuple:
    """User-built playlists minus bulk dumps; returns (membership rows, bulk playlist ids)."""
    p = playlists.loc[playlists["is_user_curated"].astype(bool)]
    sizes = p.groupby("playlist_id")["track_id"].nunique()
    bulk = set(sizes[(sizes > BULK_PLAYLIST_SHARE * n_tracks) & (sizes > BULK_PLAYLIST_MIN_TRACKS)].index)
    return p[~p["playlist_id"].isin(bulk)], bulk


_DEFAULTS = {
    "name": None, "artist": None, "album": None, "album_artist": None, "genre": None,
    "track_type": None, "year": pd.NA, "track_count": pd.NA, "total_time_ms": np.nan,
    "play_count": 0, "skip_count": 0,
    "favorited": False, "loved": False, "playlist_only": False, "explicit": False,
    "apple_music": False, "compilation": False,
}
_DATES = ["date_added", "play_date_utc", "skip_date", "release_date"]


def enrich_tracks(tracks: pd.DataFrame, playlists: pd.DataFrame, snapshot: pd.Timestamp) -> pd.DataFrame:
    """Adds every derived feature the profile (and a future model) uses. Pure function of its inputs."""
    t = tracks.copy()
    # Libraries differ in which keys Apple bothered to write; fill the gaps.
    for col, default in _DEFAULTS.items():
        if col not in t.columns:
            t[col] = default
    for col in _DATES:
        if col not in t.columns:
            t[col] = pd.NaT
        t[col] = pd.to_datetime(t[col], utc=True, errors="coerce")

    t["play_count"] = pd.to_numeric(t["play_count"], errors="coerce").fillna(0).astype(int)
    t["skip_count"] = pd.to_numeric(t["skip_count"], errors="coerce").fillna(0).astype(int)
    for col in ["favorited", "loved", "playlist_only", "explicit", "apple_music", "compilation"]:
        t[col] = t[col].fillna(False).astype(bool)

    # Identity
    t["artists"] = canonical_artists(pd.Series(
        [split_artists(a, n) for a, n in zip(t["artist"], t["name"])], index=t.index))
    t["primary_artist"] = [a[0] if a else None for a in t["artists"]]
    t["n_artists"] = t["artists"].str.len()
    t["track_key"] = [f"{a.casefold() if isinstance(a, str) else ''}|{base_title(n)}" for a, n in zip(t["primary_artist"], t["name"])]
    t["version_tags"] = t["name"].map(version_tags)
    # Title first; fall back to title + artist when the title alone is Latin or digits ("2004").
    t["script_lang"] = [
        lang if (lang := script_lang(n)) not in ("latin", "other") else script_lang(f"{n} {a}")
        for n, a in zip(t["name"], t["artist"])
    ]

    # Ownership / source
    t["is_loved"] = t["favorited"] | t["loved"]
    t["in_library"] = ~t["playlist_only"]
    t["is_local_file"] = t["track_type"].eq("File")

    # Content
    t["year"] = pd.to_numeric(t["year"], errors="coerce").astype("Int64")
    t["decade"] = t["year"] // 10 * 10
    t["duration_min"] = pd.to_numeric(t["total_time_ms"], errors="coerce") / 60_000
    t["duration_bucket"] = pd.cut(
        t["duration_min"], [0, 2, 3, 4, 5, 7, np.inf], labels=["<2m", "2-3m", "3-4m", "4-5m", "5-7m", ">7m"]
    )

    # Time
    t["days_since_added"] = (snapshot - t["date_added"]).dt.days
    t["days_since_played"] = (snapshot - t["play_date_utc"]).dt.days
    t["days_since_skipped"] = (snapshot - t["skip_date"]).dt.days
    add_day = t["date_added"].dt.floor("D")
    per_day = add_day.value_counts()
    import_days = per_day[(per_day >= BULK_IMPORT_MIN_TRACKS) & (per_day >= BULK_IMPORT_SHARE * len(t))].index
    t["added_in_bulk_import"] = add_day.isin(import_days)
    # Release-to-add gap only means "how fresh was it when you found it" for organic adds.
    t["release_age_at_add_days"] = (t["date_added"] - t["release_date"]).dt.days.where(~t["added_in_bulk_import"])
    t["freshness"] = pd.cut(
        t["release_age_at_add_days"], [-np.inf, 30, 365, 5 * 365, np.inf],
        labels=["new (<30d)", "recent (<1y)", "catalog (1-5y)", "deep catalog (>5y)"],
    )
    months_owned = (t["days_since_added"] / 30.44).clip(lower=1)
    t["plays_per_month"] = t["play_count"] / months_owned

    # Feedback
    interactions = t["play_count"] + t["skip_count"]
    t["skip_rate"] = t["skip_count"] / interactions.where(interactions > 0)
    t["likely_dislike"] = (t["skip_count"] >= 3) & (t["skip_rate"] >= 0.5)

    cur, _ = _curated(playlists, len(t))
    by_track = cur.groupby("track_id")
    t["playlist_count"] = t["track_id"].map(by_track["playlist_id"].nunique()).fillna(0).astype(int)
    names = by_track["playlist_name"].agg(lambda s: sorted(set(s)))
    t["playlist_names"] = [v if isinstance(v, list) else [] for v in t["track_id"].map(names)]

    # Implicit feedback. Can go negative (skip-heavy tracks); clip at 0 for ALS-style models.
    t["affinity"] = (
        W_PLAY * np.log1p(t["play_count"])
        - W_SKIP * np.log1p(t["skip_count"])
        + W_LOVED * t["is_loved"]
        + W_PLAYLIST * np.log1p(t["playlist_count"])
    )
    recency = (0.5 ** (t["days_since_played"] / RECENCY_HALF_LIFE_DAYS)).fillna(0)
    t["affinity_recent"] = t["affinity"] * (0.5 + 0.5 * recency)
    return t


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

def _entropy_norm(counts: pd.Series) -> Optional[float]:
    """Shannon entropy scaled to 0 (all in one bucket) .. 1 (perfectly even)."""
    c = counts[counts > 0]
    if len(c) < 2:
        return 0.0 if len(c) else None
    p = c / c.sum()
    return float(-(p * np.log(p)).sum() / math.log(len(p)))


def _effective_n(counts: pd.Series) -> Optional[float]:
    """exp(entropy): 'how many equally-played items would give this spread'."""
    c = counts[counts > 0]
    if c.empty:
        return None
    p = c / c.sum()
    return round(float(np.exp(-(p * np.log(p)).sum())), 1)


def _gini(x: pd.Series) -> Optional[float]:
    x = np.sort(np.asarray(x, dtype=float))
    if len(x) == 0 or x.sum() == 0:
        return None
    n = len(x)
    return round(float(2 * np.sum(np.arange(1, n + 1) * x) / (n * x.sum()) - (n + 1) / n), 3)


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------

def data_quality(t: pd.DataFrame) -> dict:
    """What share of tracks actually carry each signal, i.e. which features are safe to lean on."""
    share = lambda s: round(float(s.mean()), 3) if len(s) else None
    return {
        "tracks": len(t),
        "has_plays": share(t["play_count"] > 0),
        "has_skips": share(t["skip_count"] > 0),
        "has_last_played": share(t["play_date_utc"].notna()),
        "has_date_added": share(t["date_added"].notna()),
        "added_in_bulk_import": share(t["added_in_bulk_import"]),
        "has_release_date": share(t["release_date"].notna()),
        "has_genre": share(t["genre"].notna()),
        "loved": share(t["is_loved"]),
        "in_curated_playlist": share(t["playlist_count"] > 0),
        "has_rating": share(t["rating"].notna()) if "rating" in t else 0.0,
    }


def summary(lib: AppleLibrary) -> dict:
    t = lib.tracks
    added = t["date_added"].dropna()
    return {
        "user": lib.user,
        "snapshot": lib.snapshot.date().isoformat(),
        "tracks": len(t),
        "in_library": int(t["in_library"].sum()),
        "playlist_only": int(t["playlist_only"].sum()),
        "streaming_share": round(float(t["apple_music"].mean()), 3) if len(t) else None,
        "local_file_share": round(float(t["is_local_file"].mean()), 3) if len(t) else None,
        "total_plays": int(t["play_count"].sum()),
        "listening_hours": round(float((t["duration_min"].fillna(0) * t["play_count"]).sum() / 60), 1),
        "played_share": round(float((t["play_count"] > 0).mean()), 3) if len(t) else None,
        "loved": int(t["is_loved"].sum()),
        "unique_artists": int(t["artists"].explode().nunique()),
        "unique_albums": int(t["album"].nunique()),
        "unique_genres": int(t["genre"].nunique()),
        "explicit_share": round(float(t["explicit"].mean()), 3) if len(t) else None,
        "median_release_year": int(t["year"].median()) if t["year"].notna().any() else None,
        "first_added": added.min().date().isoformat() if len(added) else None,
        "last_added": added.max().date().isoformat() if len(added) else None,
        "collab_share": round(float((t["n_artists"] > 1).mean()), 3) if len(t) else None,
    }


def concentration(t: pd.DataFrame) -> dict:
    """How concentrated listening is: a few obsessions (low entropy, high gini) vs broad taste."""
    plays = t["play_count"].sort_values(ascending=False)
    total = plays.sum()

    def top_share(frac):
        k = max(1, math.ceil(len(plays) * frac))
        return round(float(plays.iloc[:k].sum() / total), 3) if total else None

    artist_plays = t.groupby("primary_artist")["play_count"].sum()
    genre_plays = t.groupby("genre")["play_count"].sum()
    return {
        "top_1pct_tracks_play_share": top_share(0.01),
        "top_10pct_tracks_play_share": top_share(0.10),
        "top_20pct_tracks_play_share": top_share(0.20),
        "track_play_gini": _gini(t["play_count"]),
        "artist_play_gini": _gini(artist_plays),
        "effective_artists": _effective_n(artist_plays),
        "effective_genres": _effective_n(genre_plays),
        "artist_entropy": _entropy_norm(artist_plays),
        "genre_entropy": _entropy_norm(genre_plays),
    }


def affinity_table(t: pd.DataFrame, key: str) -> pd.DataFrame:
    """
    Per-value engagement for any feature (artist, genre, decade, language...).
    lift > 1 means that value gets more plays than its share of the library
    would predict: what you *play* vs what you merely *save*.
    For list features (artists, version_tags, playlist_names) shares are
    among tracks that carry a value, e.g. "slowed" vs other tagged versions.
    """
    d = t.explode(key) if key in _LIST_COLS else t
    d = d.dropna(subset=[key])
    if d.empty:
        return pd.DataFrame()
    g = d.groupby(key, observed=True)
    out = pd.DataFrame({
        "tracks": g.size(),
        "plays": g["play_count"].sum(),
        "skips": g["skip_count"].sum(),
        "loved": g["is_loved"].sum(),
        "playlist_hits": g["playlist_count"].sum(),
        "affinity": g["affinity"].sum().round(2),
        "affinity_recent": g["affinity_recent"].sum().round(2),
        "first_added": g["date_added"].min(),
        "last_played": g["play_date_utc"].max(),
    })
    total_plays = out["plays"].sum()
    out["play_share"] = (out["plays"] / total_plays).round(3) if total_plays else 0.0
    out["library_share"] = (out["tracks"] / out["tracks"].sum()).round(3)
    out["lift"] = (out["play_share"] / out["library_share"]).round(2)
    out["loved_rate"] = (out["loved"] / out["tracks"]).round(3)
    out["skip_rate"] = (out["skips"] / (out["plays"] + out["skips"]).replace(0, np.nan)).round(3)
    return out.sort_values("affinity", ascending=False)


def album_behavior(t: pd.DataFrame) -> tuple:
    """Album listener vs singles listener: how much of each album you keep, and how much you play it."""
    d = t.dropna(subset=["album"]).assign(album_owner=lambda x: x["album_artist"].fillna(x["primary_artist"]))
    if d.empty:
        return pd.DataFrame(), {}
    g = d.groupby(["album", "album_owner"])
    albums = pd.DataFrame({
        "saved": g.size(),
        "album_tracks": pd.to_numeric(g["track_count"].max(), errors="coerce"),
        "plays": g["play_count"].sum(),
        "loved": g["is_loved"].sum(),
    })
    albums["completeness"] = (albums["saved"] / albums["album_tracks"]).clip(upper=1).round(2)
    full_length = albums[albums["album_tracks"] >= 4]
    stats = {
        "albums": len(albums),
        "tracks_from_singles_eps": round(float((d["track_count"].astype(float) <= 3).mean()), 3),
        "full_albums_mostly_kept": round(float((full_length["completeness"] >= 0.5).mean()), 3)
        if len(full_length) else None,
    }
    return albums.sort_values("plays", ascending=False), stats


def library_growth(t: pd.DataFrame) -> pd.DataFrame:
    """Tracks added per month: discovery rate over time."""
    s = t.dropna(subset=["date_added"]).set_index("date_added").resample("ME").size()
    return pd.DataFrame({"added": s, "cumulative": s.cumsum()})


def recency_buckets(t: pd.DataFrame) -> pd.Series:
    """How much of the library is in active rotation vs dormant."""
    b = pd.cut(t["days_since_played"], [-np.inf, 7, 30, 90, 365, np.inf],
               labels=["<=7d", "8-30d", "31-90d", "91-365d", ">1y"])
    counts = b.value_counts(sort=False)
    counts["never played"] = int(t["days_since_played"].isna().sum())
    return counts


def drift(t: pd.DataFrame, key: str, days: int = RECENT_DAYS) -> pd.DataFrame:
    """
    Share of each value in the whole library vs recent adds vs recently played.
    Positive drift = what the user is moving towards right now.
    """
    share = lambda s: s.value_counts(normalize=True)
    out = pd.DataFrame({
        "library": share(t[key]),
        "recent_adds": share(t.loc[t["days_since_added"] <= days, key]),
        "recent_plays": share(t.loc[t["days_since_played"] <= days, key]),
    }).fillna(0).round(3)
    out["drift"] = (out["recent_plays"] - out["library"]).round(3)
    return out.sort_values("drift", ascending=False)


_SHOW = ["name", "primary_artist", "genre", "play_count", "skip_count", "is_loved",
         "days_since_played", "affinity"]


def segments(t: pd.DataFrame) -> dict:
    """Track lists a recommender acts on directly (candidates, exclusions, cold items)."""
    played = t[t["play_count"] > 0]
    heavy = played["play_count"].quantile(0.75) if len(played) else np.inf
    seg = {
        # What to seed "more like this" from.
        "heavy_rotation": t[t["days_since_played"] <= 30].sort_values("affinity_recent", ascending=False),
        # Liked once, not heard lately: re-surfacing candidates.
        "forgotten_favorites": t[(t["is_loved"] | (t["play_count"] >= heavy))
                                 & (t["days_since_played"] > FORGOTTEN_AFTER_DAYS)]
        .sort_values("affinity", ascending=False),
        # Saved but never played: cold items, good for exploration slots.
        "unplayed_saves": t[t["in_library"] & (t["play_count"] == 0)].sort_values("date_added", ascending=False),
        # Explicit like without the plays to match: a signal plays alone would miss.
        "loved_rarely_played": t[t["is_loved"] & (t["play_count"] <= 2)],
        # Negative feedback: down-weight these and their near neighbours.
        "likely_dislikes": t[t["likely_dislike"]].sort_values("skip_rate", ascending=False),
    }
    return {k: v[_SHOW].reset_index(drop=True) for k, v in seg.items()}


def playlist_summary(lib: AppleLibrary) -> pd.DataFrame:
    """
    One row per user playlist. Playlist names ("Raining season", "late night")
    are free mood/context labels; `genre_coherence` near 1 means the playlist
    is one tight style, near 0 means it's a mixed bag.
    """
    p = lib.playlists.loc[lib.playlists["is_user_curated"].astype(bool)]
    if p.empty:
        return pd.DataFrame()
    _, bulk = _curated(lib.playlists, len(lib.tracks))
    cols = ["track_id", "genre", "primary_artist", "year", "is_loved", "play_count", "script_lang"]
    p = p.merge(lib.tracks[cols], on="track_id", how="left")
    rows = []
    for (pid, name), g in p.groupby(["playlist_id", "playlist_name"]):
        genres = g["genre"].value_counts()
        coherence = _entropy_norm(genres)
        rows.append({
            "playlist": name,
            "tracks": len(g),
            "bulk_dump": pid in bulk,
            "top_genre": genres.index[0] if len(genres) else None,
            "genre_coherence": round(1 - coherence, 3) if coherence is not None else None,
            "top_artists": ", ".join(g["primary_artist"].value_counts().head(3).index),
            "main_lang": g["script_lang"].mode().iat[0] if g["script_lang"].notna().any() else None,
            "median_year": g["year"].median(),
            "loved_share": round(float(g["is_loved"].mean()), 3),
            "mean_plays": round(float(g["play_count"].mean()), 1),
        })
    return pd.DataFrame(rows).sort_values("tracks", ascending=False).reset_index(drop=True)


def artist_cooccurrence(lib: AppleLibrary) -> pd.DataFrame:
    """
    Artist pairs the user put in the same playlist: a hand-labelled
    "these go together" graph for item-item similarity. Bulk dumps are
    excluded. ponytail: artist-level only; track pairs blow up on big playlists.
    """
    cur, _ = _curated(lib.playlists, len(lib.tracks))
    m = cur.merge(lib.tracks[["track_id", "primary_artist"]], on="track_id").dropna(subset=["primary_artist"])
    pairs = Counter()
    for _, g in m.groupby("playlist_id"):
        pairs.update(combinations(sorted(set(g["primary_artist"])), 2))
    out = pd.DataFrame([(a, b, n) for (a, b), n in pairs.items()],
                       columns=["artist_a", "artist_b", "shared_playlists"])
    return out.sort_values("shared_playlists", ascending=False).reset_index(drop=True)


def build_profile(lib: AppleLibrary) -> dict:
    """Every insight for one library. Tables are complete (not truncated) so they can feed a model."""
    t = lib.tracks
    albums, album_stats = album_behavior(t)
    return {
        "data_quality": data_quality(t),
        "summary": summary(lib),
        "concentration": concentration(t),
        "artists": affinity_table(t, "artists"),
        "genres": affinity_table(t, "genre"),
        "languages": affinity_table(t, "script_lang"),
        "decades": affinity_table(t, "decade").sort_index(),
        "freshness": affinity_table(t, "freshness").sort_index(),
        "durations": affinity_table(t, "duration_bucket").sort_index(),
        "versions": affinity_table(t, "version_tags"),
        "playlist_tags": affinity_table(t, "playlist_names"),
        "album_stats": album_stats,
        "albums": albums,
        "library_growth": library_growth(t),
        "recency": recency_buckets(t),
        "genre_drift": drift(t, "genre"),
        "artist_drift": drift(t, "primary_artist"),
        "segments": segments(t),
        "playlists": playlist_summary(lib),
        "artist_cooccurrence": artist_cooccurrence(lib),
    }


# ---------------------------------------------------------------------------
# Across users (the collaborative-filtering side)
# ---------------------------------------------------------------------------

def interactions(libs: list) -> pd.DataFrame:
    """
    Long user x track table across libraries, keyed by `track_key`
    (primary artist + base title), because Track IDs and Persistent IDs are
    per-library and never match across users.
    """
    cols = ["track_key", "name", "primary_artist", "genre", "play_count", "skip_count",
            "is_loved", "playlist_count", "affinity", "affinity_recent"]
    return pd.concat([lib.tracks[cols].assign(user=lib.user) for lib in libs], ignore_index=True)


def user_similarity(libs: list) -> dict:
    """Jaccard on shared tracks / artists, cosine on genre affinity: who taste-neighbours whom."""
    users = [lib.user for lib in libs]

    def jaccard(sets):
        return pd.DataFrame(
            [[len(sets[a] & sets[b]) / len(sets[a] | sets[b]) if sets[a] | sets[b] else np.nan for b in users]
             for a in users], index=users, columns=users,
        ).round(3)

    tracks = {lib.user: set(lib.tracks["track_key"]) for lib in libs}
    artists = {lib.user: set(lib.tracks["artists"].explode().dropna().str.casefold()) for lib in libs}
    genre = interactions(libs).pivot_table(index="user", columns="genre", values="affinity",
                                           aggfunc="sum", fill_value=0).clip(lower=0)
    v = genre.to_numpy()
    norm = np.linalg.norm(v, axis=1, keepdims=True)
    unit = np.divide(v, norm, out=np.zeros_like(v), where=norm > 0)
    return {
        "track_jaccard": jaccard(tracks),
        "artist_jaccard": jaccard(artists),
        "genre_cosine": pd.DataFrame(unit @ unit.T, index=genre.index, columns=genre.index).round(3),
    }


# ---------------------------------------------------------------------------
# Printing / CLI
# ---------------------------------------------------------------------------

def print_profile(profile: dict, top_n: int = 10, _indent: str = "") -> None:
    for key, val in profile.items():
        print(f"\n{_indent}== {key} ==")
        if isinstance(val, dict) and val and all(isinstance(v, (pd.DataFrame, pd.Series)) for v in val.values()):
            print_profile(val, top_n, _indent + "  ")
        elif isinstance(val, dict):
            for k, v in val.items():
                print(f"{_indent}  {k}: {v}")
        elif isinstance(val, (pd.DataFrame, pd.Series)):
            print(val.head(top_n).to_string() if len(val) else f"{_indent}  (empty)")
        else:
            print(f"{_indent}  {val}")


def main():
    parser = argparse.ArgumentParser(description="Apple Music library insights for recommendation.")
    parser.add_argument("--user", nargs="+", required=True, help="One or more usernames (Library_<user>.xml)")
    parser.add_argument("--local-dir", help="Read Library_<user>.xml from this folder instead of Hugging Face")
    parser.add_argument("--top-n", type=int, default=10)
    args = parser.parse_args()

    libs = [
        load_library(u, str(Path(args.local_dir) / f"Library_{u}.xml") if args.local_dir else None)
        for u in args.user
    ]
    for lib in libs:
        print("\n" + "#" * 70 + f"\n# {lib.user}\n" + "#" * 70)
        print_profile(build_profile(lib), args.top_n)
    if len(libs) > 1:
        print("\n" + "#" * 70 + "\n# user similarity\n" + "#" * 70)
        print_profile(user_similarity(libs))


if __name__ == "__main__":
    main()
