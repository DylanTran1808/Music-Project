"""Offline check for spotify_insights on a tiny synthetic history. Run: uv run python tests/test_spotify_insights.py"""

import pandas as pd

from music_project.analysis.apple_insights import enrich_tracks
from music_project.analysis.report import source_overlap
from music_project.analysis.spotify_insights import SpotifyHistory, build_profile, build_tracks, enrich_events


def _play(ts, name, artist, ms=200_000, start="trackdone", end="trackdone", uri=None, **kw):
    return {"ts": ts, "ms_played": ms, "conn_country": "VN", "platform": "ios", "ip_addr": "1.2.3.4",
            "master_metadata_track_name": name, "master_metadata_album_artist_name": artist,
            "master_metadata_album_album_name": "Alb", "spotify_track_uri": uri or f"spotify:track:{name}",
            "reason_start": start, "reason_end": end, "shuffle": False, "skipped": end != "trackdone",
            "offline": False, "incognito_mode": False, **kw}


def demo():
    raw = pd.DataFrame([
        # Session 1 (2026-08-01 ~20:00 local = 13:00 UTC): Fave chosen, replayed, Meh skipped early, stop
        _play("2026-08-01T13:03:20Z", "Fave (feat. B)", "A", start="clickrow"),
        _play("2026-08-01T13:06:40Z", "Fave (feat. B)", "A", start="backbtn"),
        _play("2026-08-01T13:06:50Z", "Meh", "C", ms=10_000, end="fwdbtn"),
        _play("2026-08-01T13:10:10Z", "Next", "D", ms=200_000),
        # Session 2, next day: Meh skipped again twice, then a podcast; one incognito play
        _play("2026-08-02T01:00:05Z", "Meh", "C", ms=5_000, start="clickrow", end="fwdbtn"),
        _play("2026-08-02T01:00:10Z", "Meh", "C", ms=5_000, end="fwdbtn"),
        _play("2026-08-02T01:05:00Z", "Secret", "E", incognito_mode=True),
        {"ts": "2026-08-02T01:30:00Z", "ms_played": 600_000, "episode_name": "Ep1",
         "episode_show_name": "Show", "spotify_episode_uri": "spotify:episode:1", "reason_start": "clickrow",
         "reason_end": "endplay", "conn_country": "VN"},
    ])
    raw = pd.concat([raw, raw.iloc[[0]]], ignore_index=True)  # exact duplicate row must be dropped

    e = enrich_events(raw)
    assert "ip_addr" not in e.columns                     # PII dropped
    assert len(e) == 8 and e.attrs["tz"] == "Asia/Ho_Chi_Minh"
    assert e["session_id"].nunique() == 2
    assert e.loc[0, "hour"] == 20                          # UTC 13:00 -> 20:00 local, from start time
    assert (e["kind"] == "podcast").sum() == 1

    t = build_tracks(e).set_index("name")
    assert t.loc["Fave (feat. B)", "artists"] == ["A", "B"]
    assert t.loc["Fave (feat. B)", "replays"] == 1 and t.loc["Fave (feat. B)", "chosen"] == 1
    assert t.loc["Fave (feat. B)", "loops"] == 1
    assert t.loc["Meh", "early_skips"] == 3 and t.loc["Meh", "likely_dislike"]
    assert t.loc["Secret", "affinity"] == 0                # incognito carries no weight
    assert t.loc["Fave (feat. B)", "affinity"] > t.loc["Next", "affinity"] > 0 > t.loc["Meh", "affinity"]
    assert round(t.loc["Next", "duration_min"], 2) == 3.33

    hist = SpotifyHistory("u", e, build_tracks(e), e["ts"].max(), e.attrs["tz"])
    p = build_profile(hist)
    assert p["summary"]["unique_tracks"] == 4 and p["summary"]["sessions"] == 2
    assert p["behavior"]["replay_rate"] == round(1 / 7, 3)
    assert list(p["segments"]["likely_dislikes"]["name"]) == ["Meh"]
    tt = p["track_transitions"]
    assert ((tt["from"] == "a|fave") & (tt["to"] == "c|meh")).any()
    assert p["podcasts"].loc["Show", "episodes"] == 1

    # Same song in an Apple library matches on track_key across sources.
    apple = enrich_tracks(pd.DataFrame({"track_id": [1], "name": ["Fave (feat. B)"], "artist": ["A & B"]}),
                          pd.DataFrame(columns=["playlist_id", "playlist_name", "is_user_curated", "track_id"]),
                          pd.Timestamp("2026-08-03", tz="UTC"))
    ov = source_overlap(apple, hist.tracks)
    assert ov["shared_track_count"] == 1 and "b" in ov["shared_artists"]
    print("ok")


if __name__ == "__main__":
    demo()
