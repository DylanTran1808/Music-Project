"""
spotify_insights.py

Everything Spotify's streaming-history export can tell a recommender.
Unlike the Apple Music XML (lifetime counters), this is one row per play,
so it carries *behaviour*: how a play started and ended, sessions,
time-of-day context, discovery over time, and what follows what.

Three layers:
1. `hist.events`: every play, cleaned, in local time, with session ids and
   per-play feedback flags (streamed / completed / skipped early / replayed
   / chosen by hand). ip_addr is dropped on load and never kept.
2. `hist.tracks`: one row per track with engagement counts and an
   implicit-feedback score (`affinity`), same scale and `track_key` format
   as apple_insights so both sources can feed one model.
3. `build_profile(hist)`: the taste profile.

Reading the reason fields (extended export):
- reason_end == "fwdbtn"  -> the user skipped to the next track (a real skip).
- reason_end == "endplay" -> playback stopped (closed app, picked something
  else). Spotify's `skipped` flag is True here too, but it isn't dislike,
  so `skipped` is ignored in favour of reason_end.
- reason_start == "clickrow" -> picked by hand (strong intent);
  "trackdone" -> autoplay/queue continuation; "backbtn" -> went back to replay it.
- A play counts as a stream at >= 30 s, same threshold Spotify uses.

Not in this export: genre, track duration (estimated from completed plays),
release date, likes, playlists (those are in the separate account-data
export, Playlist1.json / YourLibrary.json).

Usage:
    from music_project.analysis.spotify_insights import load_history, build_profile
    from music_project.analysis.apple_insights import print_profile

    hist = load_history("viethung")                                   # from the HF repo
    hist = load_history("viethung", path="data/raw_spot/Spotify_viethung")  # offline
    print_profile(build_profile(hist))

    uv run python -m music_project.analysis.spotify_insights --user bhuy viethung --local-dir data/raw_spot
"""

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import NamedTuple, Optional

import numpy as np
import pandas as pd

from music_project.analysis.apple_insights import (
    _effective_n, _entropy_norm, _gini, base_title, canonical_artists, print_profile, script_lang,
    split_artists, version_tags,
)
from music_project.connectors.spotify import stream_all_spotify_for_user

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

STREAM_MS = 30_000             # Spotify's own "counts as a stream" threshold
SESSION_GAP_MIN = 30           # silence longer than this starts a new session

# Per-play feedback values, summed per track then log-scaled into `affinity`.
# ponytail: hand-tuned; fit them (e.g. predict next-month streams) once a model exists.
V_STREAM = 1.0
V_CHOSEN = 0.5                 # started by clicking the track
V_REPLAY = 0.5                 # started via the back button
V_EARLY_SKIP = -0.7            # fwdbtn before 30 s
RECENCY_HALF_LIFE_DAYS = 180

RECENT_DAYS = 90
FORGOTTEN_AFTER_DAYS = 180

# ponytail: small map; add countries as users travel. Unknown -> UTC.
COUNTRY_TZ = {
    "VN": "Asia/Ho_Chi_Minh", "SG": "Asia/Singapore", "TH": "Asia/Bangkok", "JP": "Asia/Tokyo",
    "KR": "Asia/Seoul", "CN": "Asia/Shanghai", "US": "America/New_York", "GB": "Europe/London",
    "AU": "Australia/Sydney", "FR": "Europe/Paris", "DE": "Europe/Berlin",
}
DAYPARTS = [(0, "night"), (5, "morning"), (12, "afternoon"), (17, "evening"), (22, "night")]

_PII = ["ip_addr", "ip_addr_decrypted", "user_agent_decrypted", "username"]
_LEGACY = {"endTime": "ts", "artistName": "master_metadata_album_artist_name",
           "trackName": "master_metadata_track_name", "msPlayed": "ms_played"}
_DEFAULTS = {
    "ms_played": 0, "platform": None, "conn_country": None,
    "master_metadata_track_name": None, "master_metadata_album_artist_name": None,
    "master_metadata_album_album_name": None, "spotify_track_uri": None,
    "episode_name": None, "episode_show_name": None, "spotify_episode_uri": None, "audiobook_uri": None,
    "reason_start": "unknown", "reason_end": "unknown",
    "shuffle": False, "offline": False, "incognito_mode": False,
}
_LIST_COLS = {"artists", "version_tags"}


class SpotifyHistory(NamedTuple):
    user: str
    events: pd.DataFrame     # one row per play (music + podcasts), time-ordered
    tracks: pd.DataFrame     # one row per music track
    snapshot: pd.Timestamp   # last event, used as "now"
    tz: str


# ---------------------------------------------------------------------------
# Loading + enrichment
# ---------------------------------------------------------------------------

def load_history(username: str, path: Optional[str] = None, tz: Optional[str] = None) -> SpotifyHistory:
    """
    Load one user's history from the HF repo, or from a local Spotify_<user>
    folder if `path` is given. tz defaults to the user's most common connection country.
    """
    if path:
        records = []
        for f in sorted(Path(path).glob("*.json")):
            with open(f, encoding="utf-8") as fh:
                records += json.load(fh)
    else:
        records = list(stream_all_spotify_for_user(username))
    events = enrich_events(pd.DataFrame(records), tz)
    return SpotifyHistory(username, events, build_tracks(events), events["ts"].max(), events.attrs["tz"])


def _daypart(hour: int) -> str:
    return [name for start, name in DAYPARTS if hour >= start][-1]


def enrich_events(raw: pd.DataFrame, tz: Optional[str] = None) -> pd.DataFrame:
    """Cleans raw export rows into typed, local-time, sessionised play events with feedback flags."""
    e = raw.drop(columns=[c for c in _PII if c in raw.columns]).rename(columns=_LEGACY)
    for col, default in _DEFAULTS.items():
        if col not in e.columns:
            e[col] = default
    e["ts"] = pd.to_datetime(e["ts"], utc=True, errors="coerce")
    e = e.dropna(subset=["ts"]).drop_duplicates().sort_values("ts").reset_index(drop=True)

    for col in ["shuffle", "offline", "incognito_mode"]:
        e[col] = e[col].fillna(False).astype(bool)
    e["reason_start"] = e["reason_start"].fillna("unknown")
    e["reason_end"] = e["reason_end"].fillna("unknown")
    e["ms_played"] = pd.to_numeric(e["ms_played"], errors="coerce").fillna(0).astype(int)

    e["kind"] = np.select(
        [e["spotify_episode_uri"].notna() | e["episode_name"].notna(),
         e["audiobook_uri"].notna(),
         e["master_metadata_track_name"].notna()],
        ["podcast", "audiobook", "music"], default="unknown",
    )
    e = e.rename(columns={"master_metadata_track_name": "name",
                          "master_metadata_album_artist_name": "artist",
                          "master_metadata_album_album_name": "album"})

    # Identity (music rows); track_key matches apple_insights for cross-source joins.
    primary = {a: (split_artists(a, None) or [""])[0].casefold() for a in e["artist"].dropna().unique()}
    e["track_key"] = e["artist"].map(primary).fillna("") + "|" + e["name"].map(base_title)
    e["track_id"] = e["spotify_track_uri"].fillna("key:" + e["track_key"])

    # Time: ts is when the play *ended*.
    if tz is None:
        country = e["conn_country"].mode()
        tz = COUNTRY_TZ.get(country.iat[0], "UTC") if len(country) else "UTC"
    e.attrs["tz"] = tz
    e["start"] = e["ts"] - pd.to_timedelta(e["ms_played"], unit="ms")
    local = e["start"].dt.tz_convert(tz)
    e["local_date"] = local.dt.date
    e["hour"] = local.dt.hour
    e["weekday"] = local.dt.dayofweek
    e["daypart"] = e["hour"].map(_daypart)
    e["month"] = local.dt.tz_localize(None).dt.to_period("M")
    e["year"] = local.dt.year

    # Sessions: a gap of > SESSION_GAP_MIN between one play ending and the next starting.
    gap = e["start"] - e["ts"].shift()
    e["session_id"] = (gap > pd.Timedelta(minutes=SESSION_GAP_MIN)).cumsum()
    e["is_session_start"] = e["session_id"].ne(e["session_id"].shift())
    e["is_loop"] = e["track_id"].eq(e["track_id"].shift()) & ~e["is_session_start"]

    # Feedback flags
    e["streamed"] = e["ms_played"] >= STREAM_MS
    e["completed"] = e["reason_end"].eq("trackdone")
    e["skipped_fwd"] = e["reason_end"].eq("fwdbtn")
    e["early_skip"] = e["skipped_fwd"] & ~e["streamed"]
    e["replay"] = e["reason_start"].eq("backbtn")
    e["chosen"] = e["reason_start"].eq("clickrow")
    e["autoplay"] = e["reason_start"].eq("trackdone")

    # Incognito plays are the user saying "don't learn from this": zero weight.
    value = (V_STREAM * e["streamed"] + V_CHOSEN * e["chosen"] + V_REPLAY * e["replay"]
             + V_EARLY_SKIP * e["early_skip"]) * ~e["incognito_mode"]
    age_days = (e["ts"].max() - e["ts"]).dt.total_seconds() / 86_400
    e["value"] = value
    e["value_recent"] = value * 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)
    return e


def _signed_log(x: pd.Series) -> pd.Series:
    return np.sign(x) * np.log1p(x.abs())


def build_tracks(events: pd.DataFrame) -> pd.DataFrame:
    """One row per music track: engagement counts, lifecycle, text features, affinity."""
    m = events[events["kind"] == "music"]
    if m.empty:
        return pd.DataFrame()
    snapshot = events["ts"].max()
    g = m.groupby("track_id")
    t = pd.DataFrame({
        "name": g["name"].first(),
        "artist": g["artist"].first(),
        "album": g["album"].first(),
        "track_key": g["track_key"].first(),
        "plays": g.size(),
        "streams": g["streamed"].sum(),
        "completes": g["completed"].sum(),
        "skips": g["skipped_fwd"].sum(),
        "early_skips": g["early_skip"].sum(),
        "replays": g["replay"].sum(),
        "chosen": g["chosen"].sum(),
        "loops": g["is_loop"].sum(),
        "session_starts": g["is_session_start"].sum(),
        "shuffle_share": g["shuffle"].mean().round(3),
        "ms_played": g["ms_played"].sum(),
        "first_played": g["ts"].min(),
        "last_played": g["ts"].max(),
        "active_days": g["local_date"].nunique(),
        "score": g["value"].sum(),
        "score_recent": g["value_recent"].sum(),
    })
    # No duration field: the longest play that ended naturally is a good estimate.
    t["duration_min"] = m[m["completed"]].groupby("track_id")["ms_played"].max() / 60_000
    t["completion_rate"] = (t["completes"] / t["plays"]).round(3)
    t["skip_rate"] = (t["skips"] / t["plays"]).round(3)
    t["early_skip_rate"] = (t["early_skips"] / t["plays"]).round(3)
    t["likely_dislike"] = (t["plays"] >= 3) & (t["early_skip_rate"] >= 0.5)
    t["days_since_played"] = (snapshot - t["last_played"]).dt.days
    t["days_since_first"] = (snapshot - t["first_played"]).dt.days
    t["lifespan_days"] = (t["last_played"] - t["first_played"]).dt.days

    # Spotify only gives the album artist; features come from "(feat. X)" in titles.
    t["artists"] = canonical_artists(pd.Series(
        [split_artists(a, n) for a, n in zip(t["artist"], t["name"])], index=t.index))
    t["primary_artist"] = [a[0] if a else None for a in t["artists"]]
    t["n_artists"] = t["artists"].str.len()
    t["version_tags"] = t["name"].map(version_tags)
    t["script_lang"] = [
        lang if (lang := script_lang(n)) not in ("latin", "other") else script_lang(f"{n} {a}")
        for n, a in zip(t["name"], t["artist"])
    ]
    t["affinity"] = _signed_log(t["score"])
    t["affinity_recent"] = _signed_log(t["score_recent"])
    return t.reset_index()


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------

def _share(s) -> Optional[float]:
    return round(float(s.mean()), 3) if len(s) else None


def data_quality(hist: SpotifyHistory) -> dict:
    e = hist.events
    return {
        "events": len(e),
        "music_events": int((e["kind"] == "music").sum()),
        "podcast_events": int((e["kind"] == "podcast").sum()),
        "first_event": e["ts"].min().date().isoformat(),
        "last_event": e["ts"].max().date().isoformat(),
        "timezone": hist.tz,
        "has_reason_fields": _share(e["reason_end"] != "unknown"),
        "has_track_uri": _share(e.loc[e["kind"] == "music", "spotify_track_uri"].notna()),
        "incognito_share": _share(e["incognito_mode"]),
        "offline_share": _share(e["offline"]),
        "tracks_with_est_duration": _share(hist.tracks["duration_min"].notna()),
    }


def summary(hist: SpotifyHistory) -> dict:
    e, t = hist.events, hist.tracks
    m = e[e["kind"] == "music"]
    active_days = m["local_date"].nunique()
    return {
        "user": hist.user,
        "streams": int(m["streamed"].sum()),
        "music_hours": round(float(m["ms_played"].sum() / 3.6e6), 1),
        "podcast_hours": round(float(e.loc[e["kind"] == "podcast", "ms_played"].sum() / 3.6e6), 1),
        "unique_tracks": len(t),
        "unique_artists": int(t["artists"].explode().nunique()),
        "unique_albums": int(t["album"].nunique()),
        "active_days": active_days,
        "minutes_per_active_day": round(float(m["ms_played"].sum() / 60_000 / max(active_days, 1)), 1),
        "sessions": int(e["session_id"].nunique()),
        "collab_share": _share(t["n_artists"] > 1),
        "platforms": platform_mix(e).round(3).to_dict(),
    }


def platform_mix(e: pd.DataFrame) -> pd.Series:
    """Share of listening time per device family."""
    p = e["platform"].fillna("unknown").str.lower()
    family = np.select(
        [p.str.contains("ios|iphone|ipad"), p.str.contains("android"), p.str.contains("osx|mac"),
         p.str.contains("windows"), p.str.contains("web"), p.str.contains("cast|sonos|speaker|tv")],
        ["ios", "android", "mac", "windows", "web", "speaker/tv"], default="other",
    )
    return e.groupby(family)["ms_played"].sum().pipe(lambda s: s / s.sum()).sort_values(ascending=False)


def behavior(e: pd.DataFrame) -> dict:
    """How the user listens: lean-back (autoplay, shuffle, completes) vs lean-in (picks, skips, replays)."""
    m = e[e["kind"] == "music"]
    return {
        "stream_rate": _share(m["streamed"]),
        "completion_rate": _share(m["completed"]),
        "skip_rate": _share(m["skipped_fwd"]),
        "early_skip_rate": _share(m["early_skip"]),
        "stopped_rate": _share(m["reason_end"] == "endplay"),
        "chosen_rate": _share(m["chosen"]),
        "autoplay_rate": _share(m["autoplay"]),
        "replay_rate": _share(m["replay"]),
        "loop_rate": _share(m["is_loop"]),
        "shuffle_rate": _share(m["shuffle"]),
        "skip_rate_on_shuffle": _share(m.loc[m["shuffle"], "skipped_fwd"]),
        "skip_rate_off_shuffle": _share(m.loc[~m["shuffle"], "skipped_fwd"]),
        "offline_rate": _share(m["offline"]),
    }


def concentration(t: pd.DataFrame) -> dict:
    """A few obsessions (high gini, low effective counts) vs broad taste."""
    s = t["streams"].sort_values(ascending=False)
    total = s.sum()
    top = lambda frac: round(float(s.iloc[:max(1, int(np.ceil(len(s) * frac)))].sum() / total), 3) if total else None
    artist_streams = t.groupby("primary_artist")["streams"].sum()
    return {
        "top_1pct_tracks_stream_share": top(0.01),
        "top_10pct_tracks_stream_share": top(0.10),
        "top_20pct_tracks_stream_share": top(0.20),
        "track_stream_gini": _gini(t["streams"]),
        "artist_stream_gini": _gini(artist_streams),
        "effective_artists": _effective_n(artist_streams),
        "artist_entropy": _entropy_norm(artist_streams),
    }


def affinity_table(t: pd.DataFrame, key: str) -> pd.DataFrame:
    """
    Per-value engagement from the track table (artist, album, language, version...).
    depth = stream_share / track_share: > 1 means few tracks played a lot (a deep
    fan), < 1 means many tracks each played a little (sampling).
    """
    d = t.explode(key) if key in _LIST_COLS else t
    d = d.dropna(subset=[key])
    if d.empty:
        return pd.DataFrame()
    g = d.groupby(key, observed=True)
    out = pd.DataFrame({
        "tracks": g.size(),
        "streams": g["streams"].sum(),
        "hours": (g["ms_played"].sum() / 3.6e6).round(1),
        "skips": g["skips"].sum(),
        "early_skips": g["early_skips"].sum(),
        "replays": g["replays"].sum(),
        "chosen": g["chosen"].sum(),
        "affinity": g["affinity"].sum().round(2),
        "affinity_recent": g["affinity_recent"].sum().round(2),
        "first_played": g["first_played"].min(),
        "last_played": g["last_played"].max(),
    })
    plays = g["plays"].sum()
    total = out["streams"].sum()
    out["stream_share"] = (out["streams"] / total).round(3) if total else 0.0
    out["track_share"] = (out["tracks"] / out["tracks"].sum()).round(3)
    out["depth"] = (out["stream_share"] / out["track_share"]).round(2)
    out["skip_rate"] = (out["skips"] / plays).round(3)
    out["early_skip_rate"] = (out["early_skips"] / plays).round(3)
    return out.sort_values("affinity", ascending=False)


def time_of_day(e: pd.DataFrame) -> pd.DataFrame:
    """Per local hour: listening hours, streams, skip rate. Skips that rise at certain hours = context mismatch."""
    m = e[e["kind"] == "music"]
    g = m.groupby("hour")
    return pd.DataFrame({
        "hours": (g["ms_played"].sum() / 3.6e6).round(1),
        "streams": g["streamed"].sum(),
        "skip_rate": g["skipped_fwd"].mean().round(3),
        "chosen_rate": g["chosen"].mean().round(3),
    }).reindex(range(24), fill_value=0)


def weekly_heatmap(e: pd.DataFrame) -> pd.DataFrame:
    """Listening hours, weekday (rows) x local hour (columns)."""
    m = e[e["kind"] == "music"]
    h = m.pivot_table(index="weekday", columns="hour", values="ms_played", aggfunc="sum", fill_value=0) / 3.6e6
    h = h.reindex(index=range(7), columns=range(24), fill_value=0).round(1)
    h.index = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return h


def daypart_artists(hist: SpotifyHistory, top: int = 5, min_streams: int = 5) -> pd.DataFrame:
    """
    Which artists belong to which part of the day: context features for a
    time-aware recommender. lift > 1 = over-played in that daypart vs overall.
    """
    m = _streams_with_artist(hist).dropna(subset=["artist"]).rename(columns={"artist": "primary_artist"})
    overall = m["primary_artist"].value_counts(normalize=True)
    rows = []
    for part, g in m.groupby("daypart"):
        counts = g["primary_artist"].value_counts()
        share = counts / counts.sum()
        for artist in counts[counts >= min_streams].head(top).index:
            rows.append({"daypart": part, "artist": artist, "streams": int(counts[artist]),
                         "share": round(float(share[artist]), 3),
                         "lift": round(float(share[artist] / overall[artist]), 2)})
    if not rows:
        return pd.DataFrame()
    order = {name: i for i, (_, name) in enumerate(DAYPARTS[:4])}
    out = pd.DataFrame(rows).assign(_o=lambda d: d["daypart"].map(order))
    return out.sort_values(["_o", "streams"], ascending=[True, False]).drop(columns="_o").reset_index(drop=True)


def monthly(hist: SpotifyHistory) -> pd.DataFrame:
    """
    Per month: volume plus discovery. exploration_share = share of streams on
    tracks first heard that month (high = exploring, low = replaying favourites).
    """
    m = hist.events[hist.events["kind"] == "music"]
    first_month = m.groupby("track_id")["month"].transform("min")
    first_artist_month = m.groupby("artist")["month"].transform("min")
    m = m.assign(new_track=first_month.eq(m["month"]), new_artist=first_artist_month.eq(m["month"]))
    g = m.groupby("month")
    out = pd.DataFrame({
        "hours": (g["ms_played"].sum() / 3.6e6).round(1),
        "streams": g["streamed"].sum(),
        "unique_tracks": g["track_id"].nunique(),
        "new_tracks": m[m["new_track"]].groupby("month")["track_id"].nunique(),
        "new_artists": m[m["new_artist"]].groupby("month")["artist"].nunique(),
        "exploration_share": (m[m["new_track"]].groupby("month")["streamed"].sum() / g["streamed"].sum()).round(3),
        "skip_rate": g["skipped_fwd"].mean().round(3),
    }).fillna(0)
    return out.astype({"new_tracks": int, "new_artists": int})


def _streams_with_artist(hist: SpotifyHistory) -> pd.DataFrame:
    """Streamed music events with the canonical primary artist from the track table."""
    m = hist.events[(hist.events["kind"] == "music") & hist.events["streamed"]]
    return m.assign(artist=m["track_id"].map(hist.tracks.set_index("track_id")["primary_artist"]))


def top_artist_by_year(hist: SpotifyHistory, top: int = 3) -> pd.DataFrame:
    """Top artists per year: the long-range taste timeline."""
    m = _streams_with_artist(hist)
    counts = m.groupby(["year", "artist"]).size().rename("streams").reset_index()
    counts["share"] = (counts["streams"] / counts.groupby("year")["streams"].transform("sum")).round(3)
    return counts.sort_values(["year", "streams"], ascending=[True, False]).groupby("year").head(top) \
        .reset_index(drop=True)


def sessions(hist: SpotifyHistory) -> tuple:
    """Per-session table + summary stats. Session starters are what the user reaches for first."""
    e = hist.events
    g = e.groupby("session_id")
    s = pd.DataFrame({
        "start": g["start"].min(),
        "minutes": (g["ms_played"].sum() / 60_000).round(1),
        "plays": g.size(),
        "streams": g["streamed"].sum(),
        "skip_rate": g["skipped_fwd"].mean().round(3),
        "music_share": g["kind"].apply(lambda k: (k == "music").mean()).round(3),
        "daypart": g["daypart"].first(),
    })
    starts = e[e["is_session_start"] & (e["kind"] == "music")]
    stats = {
        "sessions": len(s),
        "median_minutes": float(s["minutes"].median()) if len(s) else None,
        "median_plays": float(s["plays"].median()) if len(s) else None,
        "sessions_per_active_day": round(len(s) / max(e["local_date"].nunique(), 1), 2),
        "long_sessions_share_60m": _share(s["minutes"] >= 60),
    }
    starters = starts.groupby(["name", "artist"]).size().rename("session_starts") \
        .sort_values(ascending=False).reset_index()
    return s, stats, starters


def drift(hist: SpotifyHistory, days: int = RECENT_DAYS) -> pd.DataFrame:
    """Artist stream share: all-time vs last `days`. Positive drift = trending up for this user."""
    m = _streams_with_artist(hist)
    recent = m[m["ts"] >= hist.snapshot - pd.Timedelta(days=days)]
    out = pd.DataFrame({
        "all_time": m["artist"].value_counts(normalize=True),
        "recent": recent["artist"].value_counts(normalize=True),
    }).fillna(0).round(3)
    out["drift"] = (out["recent"] - out["all_time"]).round(3)
    return out.sort_values("drift", ascending=False)


_SHOW = ["name", "artist", "plays", "streams", "skips", "replays", "chosen", "days_since_played", "affinity"]


def segments(t: pd.DataFrame) -> dict:
    """Track lists a recommender acts on directly."""
    liked = t[t["affinity"] > 0]
    fave = liked["affinity"].quantile(0.9) if len(liked) else np.inf
    seg = {
        "heavy_rotation": t[t["days_since_played"] <= 30].sort_values("affinity_recent", ascending=False),
        # First heard recently and already sticking: fresh seeds.
        "new_obsessions": t[(t["days_since_first"] <= 30) & (t["streams"] >= 5)]
        .sort_values("streams", ascending=False),
        # Used to be top-10% and gone quiet: re-surfacing candidates.
        "forgotten_favorites": t[(t["affinity"] >= fave) & (t["days_since_played"] > FORGOTTEN_AFTER_DAYS)]
        .sort_values("affinity", ascending=False),
        # Went back to hear it again: strongest implicit "love" in this export.
        "most_replayed": t[t["replays"] >= 2].sort_values("replays", ascending=False),
        # Heard once or twice, never came back: weak/unknown, not negative.
        "sampled_once": t[(t["plays"] <= 2) & (t["days_since_first"] > 30)].sort_values("last_played", ascending=False),
        "likely_dislikes": t[t["likely_dislike"]].sort_values("early_skip_rate", ascending=False),
    }
    return {k: v[_SHOW].reset_index(drop=True) for k, v in seg.items()}


def transitions(hist: SpotifyHistory, level: str = "track") -> pd.DataFrame:
    """
    What the user plays next, within a session: a sequential co-listen graph
    for session-based / next-track recommendation. `kept` counts transitions
    where the next track was streamed (>= 30 s), i.e. the pairing worked.
    Loops (same item twice) are excluded.
    """
    e = hist.events
    col = "track_key" if level == "track" else "artist"
    m = e[e["kind"] == "music"]
    prev = m[col].shift()
    same_session = m["session_id"].eq(m["session_id"].shift())
    mask = same_session & prev.notna() & m[col].ne(prev)
    pairs = pd.DataFrame({"from": prev[mask], "to": m.loc[mask, col], "kept": m.loc[mask, "streamed"]})
    if pairs.empty:
        return pd.DataFrame(columns=["from", "to", "count", "kept"])
    out = pairs.groupby(["from", "to"]).agg(count=("kept", "size"), kept=("kept", "sum")).reset_index()
    return out.sort_values(["kept", "count"], ascending=False).reset_index(drop=True)


def podcasts(e: pd.DataFrame) -> pd.DataFrame:
    p = e[e["kind"] == "podcast"]
    if p.empty:
        return pd.DataFrame()
    g = p.groupby("episode_show_name")
    return pd.DataFrame({"episodes": g["spotify_episode_uri"].nunique(),
                         "hours": (g["ms_played"].sum() / 3.6e6).round(1)}).sort_values("hours", ascending=False)


def build_profile(hist: SpotifyHistory) -> dict:
    """Every insight for one history. Tables are complete (not truncated) so they can feed a model."""
    t, e = hist.tracks, hist.events
    session_table, session_stats, starters = sessions(hist)
    return {
        "data_quality": data_quality(hist),
        "summary": summary(hist),
        "behavior": behavior(e),
        "concentration": concentration(t),
        "tracks": t.sort_values("affinity", ascending=False)[_SHOW],
        "artists": affinity_table(t, "artists"),
        "albums": affinity_table(t, "album"),
        "languages": affinity_table(t, "script_lang"),
        "versions": affinity_table(t, "version_tags"),
        "time_of_day": time_of_day(e),
        "weekly_heatmap": weekly_heatmap(e),
        "daypart_artists": daypart_artists(hist),
        "monthly": monthly(hist),
        "top_artists_by_year": top_artist_by_year(hist),
        "session_stats": session_stats,
        "sessions": session_table,
        "session_starters": starters,
        "artist_drift": drift(hist),
        "segments": segments(t),
        "track_transitions": transitions(hist, "track"),
        "artist_transitions": transitions(hist, "artist"),
        "podcasts": podcasts(e),
    }


# ---------------------------------------------------------------------------
# Across users
# ---------------------------------------------------------------------------

def interactions(hists: list) -> pd.DataFrame:
    """Long user x track table, keyed by track_key (same format as apple_insights.interactions)."""
    cols = ["track_key", "name", "primary_artist", "plays", "streams", "skips", "replays",
            "affinity", "affinity_recent"]
    return pd.concat([h.tracks[cols].assign(user=h.user) for h in hists], ignore_index=True)


def user_similarity(hists: list) -> dict:
    """Jaccard on tracks / artists, cosine on artist affinity vectors."""
    users = [h.user for h in hists]

    def jaccard(sets):
        return pd.DataFrame(
            [[len(sets[a] & sets[b]) / len(sets[a] | sets[b]) if sets[a] | sets[b] else np.nan for b in users]
             for a in users], index=users, columns=users,
        ).round(3)

    tracks = {h.user: set(h.tracks["track_key"]) for h in hists}
    artists = {h.user: set(h.tracks["artists"].explode().dropna().str.casefold()) for h in hists}
    vec = interactions(hists).assign(artist=lambda d: d["primary_artist"].str.casefold()) \
        .pivot_table(index="user", columns="artist", values="affinity", aggfunc="sum", fill_value=0).clip(lower=0)
    v = vec.to_numpy()
    norm = np.linalg.norm(v, axis=1, keepdims=True)
    unit = np.divide(v, norm, out=np.zeros_like(v), where=norm > 0)
    return {
        "track_jaccard": jaccard(tracks),
        "artist_jaccard": jaccard(artists),
        "artist_cosine": pd.DataFrame(unit @ unit.T, index=vec.index, columns=vec.index).round(3),
    }


def main():
    parser = argparse.ArgumentParser(description="Spotify streaming-history insights for recommendation.")
    parser.add_argument("--user", nargs="+", required=True, help="One or more usernames (Spotify_<user>/)")
    parser.add_argument("--local-dir", help="Read Spotify_<user>/*.json from this folder instead of Hugging Face")
    parser.add_argument("--tz", help="Override the timezone (default: inferred from conn_country)")
    parser.add_argument("--top-n", type=int, default=10)
    args = parser.parse_args()

    hists = [
        load_history(u, str(Path(args.local_dir) / f"Spotify_{u}") if args.local_dir else None, args.tz)
        for u in args.user
    ]
    for h in hists:
        print("\n" + "#" * 70 + f"\n# {h.user}\n" + "#" * 70)
        print_profile(build_profile(h), args.top_n)
    if len(hists) > 1:
        print("\n" + "#" * 70 + "\n# user similarity\n" + "#" * 70)
        print_profile(user_similarity(hists))


if __name__ == "__main__":
    main()
