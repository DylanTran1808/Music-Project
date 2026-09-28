"""
Language groups (vi / en / other / unknown) for artists and tracks.

Metadata never says what language a song is *sung* in, so this combines the best signals
we have, strongest first, per artist:
  vi     any Vietnamese-script title, MusicBrainz country VN or main release language vi,
         or (failing everything else) credited on a track with a Vietnamese artist
  other  East Asian artist (KR/JP/CN/TW/HK: K-pop with English titles stays out), mostly
         Korean/Japanese/Chinese-script titles, or a non-English main release language
  en     English main release language (any country: Swedish EDM sings in English),
         or an English-speaking country; for artists MusicBrainz doesn't know,
         langdetect on their titles when it's >= 90% sure
  unknown  none of the above
A track follows its title's script when that's Vietnamese / Korean / Japanese / Chinese,
otherwise its primary artist. Known ceiling: English-titled music by artists MusicBrainz
doesn't know (e.g. Japanese game composers) can land in "en".
"""

from collections import Counter, defaultdict
from typing import Optional

from langdetect import DetectorFactory, LangDetectException, detect_langs
from psycopg2.extras import execute_values

DetectorFactory.seed = 0  # langdetect is random otherwise
EAST_ASIA = {"KR", "JP", "CN", "TW", "HK"}
ENGLISH_SPEAKING = {"US", "GB", "CA", "AU", "IE", "NZ"}
CJK = {"ko", "ja", "zh"}
MIN_DETECT_PROB = 0.9


def artist_language(country: Optional[str], release_languages: list, scripts: dict,
                    vi_collaborator: bool, detected: Optional[tuple]) -> str:
    """scripts: title-script counts of the artist's tracks; detected: (lang, prob) from langdetect or None."""
    first = release_languages[0] if release_languages else None
    if country == "VN" or first == "vi" or scripts.get("vi"):
        return "vi"
    if country in EAST_ASIA or first in CJK or sum(scripts.get(s, 0) for s in CJK) > scripts.get("latin", 0):
        return "other"
    if first == "en" or (first is None and country in ENGLISH_SPEAKING):
        return "en"
    if first:
        return "other"
    if vi_collaborator:
        return "vi"
    if detected and detected[0] == "en" and detected[1] >= MIN_DETECT_PROB:
        return "en"
    return "unknown"


def _detect(titles) -> Optional[tuple]:
    try:
        top = detect_langs(" . ".join(titles))[0]
        return (top.lang, top.prob)
    except LangDetectException:
        return None


def classify_languages(conn) -> dict:
    """Recomputes artists.lang_group and tracks.lang_group; returns artist counts per group."""
    with conn, conn.cursor() as cur:
        cur.execute("SELECT id, country, release_languages FROM artists")
        artists = {aid: (country, langs) for aid, country, langs in cur.fetchall()}
        cur.execute("SELECT ta.artist_id, ta.track_id, t.lang, t.title FROM track_artists ta JOIN tracks t ON t.id = ta.track_id")
        credits = cur.fetchall()

    scripts, titles, on_track = defaultdict(Counter), defaultdict(list), defaultdict(set)
    for aid, tid, lang, title in credits:
        scripts[aid][lang or "other"] += 1
        titles[aid].append(title)
        on_track[tid].add(aid)

    base = {aid: artist_language(c, langs, scripts[aid], False, None) for aid, (c, langs) in artists.items()}
    vi_collab = {aid for members in on_track.values() if any(base[a] == "vi" for a in members) for aid in members}
    groups = {}
    for aid, (country, langs) in artists.items():
        group = base[aid]
        if group == "unknown":
            group = artist_language(country, langs, scripts[aid], aid in vi_collab,
                                    _detect(titles[aid]) if titles[aid] else None)
        groups[aid] = group

    with conn, conn.cursor() as cur:
        execute_values(cur, "UPDATE artists a SET lang_group = v.g FROM (VALUES %s) AS v(id, g) WHERE a.id = v.id",
                       list(groups.items()), page_size=1000)
        cur.execute("""
            UPDATE tracks t SET lang_group = CASE
                WHEN t.lang = 'vi' THEN 'vi'
                WHEN t.lang IN ('ko', 'ja', 'zh') THEN 'other'
                ELSE coalesce((SELECT a.lang_group FROM track_artists ta JOIN artists a ON a.id = ta.artist_id
                               WHERE ta.track_id = t.id AND ta.position = 0), 'unknown') END""")
    return dict(Counter(groups.values()))
