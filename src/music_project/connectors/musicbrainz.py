"""
musicbrainz.py

Artist demographics from MusicBrainz (read-only, no login). Every request carries a
User-Agent with the MB_CONTACT from .env, as MusicBrainz requires, and requests are
spaced >= 1.1 s apart; a 503 (their rate-limit answer) is retried with backoff.

    search_artist(name)       -> candidate dicts (MB search results)
    artist_releases(mb_id)    -> release dicts (title, date, text-representation.language)
    pick_match(...)           -> ("auto" | "ambiguous" | "not_found", candidate or None)
    artist_fields(candidate)  -> columns for the artists table
    release_summary(releases) -> release_languages (ISO 639-1, most frequent first), first_release_year

Release languages describe the *titles* as MusicBrainz tagged them, not necessarily what is
sung (K-pop releases with English titles count as English). first_release_year is the earliest
release MusicBrainz knows about, a lower bound on the real debut.
"""

import os
import re
import time
import unicodedata
from collections import Counter
from datetime import date
from typing import Callable, Optional

import pycountry
import requests
from dotenv import load_dotenv

from music_project.analysis.apple_insights import base_title

load_dotenv()

API = "https://musicbrainz.org/ws/2"
MIN_INTERVAL = 1.1  # seconds between requests; MusicBrainz allows ~1/s per client
MAX_TIE_BREAK = 3   # same-name candidates whose releases are checked against our albums
NOT_LANGUAGES = {"mul", "zxx", "und", "mis"}  # multiple / no linguistic content / undetermined / uncoded
TYPES = {"person", "group", "orchestra", "choir", "character", "other"}
ENSEMBLES = {"group", "orchestra", "choir"}
_last_call = 0.0


def user_agent() -> str:
    contact = os.getenv("MB_CONTACT")
    if not contact:
        raise RuntimeError("set MB_CONTACT in .env: a URL or email MusicBrainz can reach you at")
    return f"Music-Project/0.1 ( {contact} )"


def _get(path: str, **params) -> dict:
    global _last_call
    for attempt in range(6):
        wait = _last_call + MIN_INTERVAL * 2 ** attempt - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
        r = requests.get(f"{API}/{path}", params={**params, "fmt": "json"},
                         headers={"User-Agent": user_agent()}, timeout=30)
        if r.status_code != 503:
            r.raise_for_status()
            return r.json()
    r.raise_for_status()


def search_artist(name: str, limit: int = 5) -> list:
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    return _get("artist/", query=f'artist:"{escaped}"', limit=limit).get("artists", [])


def artist_releases(mb_id: str) -> list:
    return _get("release", artist=mb_id, limit=100).get("releases", [])


def _norm(s: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", s).casefold().split())


def _names(candidate: dict) -> set:
    return {_norm(candidate.get("name", ""))} | {_norm(a["name"]) for a in candidate.get("aliases", [])}


def pick_match(name: str, candidates: list, album_titles, releases_of: Callable[[str], list]) -> tuple:
    """
    auto:      exactly one candidate has this name (or alias), or several do and exactly one
               of them has a release titled like one of our albums for the artist;
    ambiguous: several same-name candidates and no unique album match (the user decides);
    not_found: no candidate with this name.
    """
    same = [c for c in candidates if _norm(name) in _names(c)]
    if not same:
        return ("not_found", None)
    if len(same) == 1:
        return ("auto", same[0])
    ours = {base_title(t) for t in album_titles if t}
    hits = [c for c in same[:MAX_TIE_BREAK]
            if ours & {base_title(r["title"]) for r in releases_of(c["id"]) if r.get("title")}]
    return ("auto", hits[0]) if len(hits) == 1 else ("ambiguous", None)


def _date(s: Optional[str]) -> Optional[date]:
    """MusicBrainz dates are YYYY, YYYY-MM or YYYY-MM-DD; missing parts become 1."""
    m = re.fullmatch(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", s or "")
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2] or 1), int(m[3] or 1))
    except ValueError:
        return None


def artist_fields(c: dict) -> dict:
    kind = (c.get("type") or "").lower() or None
    kind = kind if kind in TYPES or kind is None else "other"
    gender = (c.get("gender") or "").lower().replace("-", "_").replace(" ", "_")
    if kind in ENSEMBLES:
        gender = "not_applicable"
    elif gender not in {"male", "female", "non_binary", "other"}:
        gender = None
    country = c.get("country")
    life = c.get("life-span") or {}
    return {
        "mb_id": c["id"],
        "artist_type": kind,
        "gender": gender,
        "country": country if isinstance(country, str) and re.fullmatch(r"[A-Z]{2}", country) else None,
        "area": (c.get("area") or {}).get("name"),
        "birth_area": (c.get("begin-area") or {}).get("name"),
        "begin_date": _date(life.get("begin")),
        "end_date": _date(life.get("end")),
        "tags": [t["name"] for t in sorted(c.get("tags", []), key=lambda t: -t.get("count", 0))][:10],
    }


def _iso1(code: str) -> str:
    lang = pycountry.languages.get(alpha_3=code)
    return getattr(lang, "alpha_2", code) if lang else code


def release_summary(releases: list) -> dict:
    langs = Counter(_iso1(code) for r in releases
                    if (code := (r.get("text-representation") or {}).get("language")) and code not in NOT_LANGUAGES)
    years = [int(r["date"][:4]) for r in releases if (r.get("date") or "")[:4].isdigit()]
    return {"release_languages": [lang for lang, _ in langs.most_common()], "first_release_year": min(years, default=None)}


def candidate_summary(c: dict) -> dict:
    """JSON-safe summary of a search hit, kept on the artist row for manual review."""
    f = artist_fields(c)
    return {"mb_id": c["id"], "name": c.get("name"), "disambiguation": c.get("disambiguation"),
            "score": c.get("score"), "artist_type": f["artist_type"], "gender": f["gender"],
            "country": f["country"], "area": f["area"], "birth_area": f["birth_area"],
            "begin_date": f["begin_date"].isoformat() if f["begin_date"] else None,
            "end_date": f["end_date"].isoformat() if f["end_date"] else None}
