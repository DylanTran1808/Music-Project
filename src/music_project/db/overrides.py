"""
Committed catalogue fixes, so hand research survives rebuilding p_music.

overrides/artists.json  artist facts set outside the MusicBrainz lookup (web research, the user's
                        decisions), keyed by artist name. Written by export_artists().
overrides/credits.json  per-song credit fixes, keyed by tracks.match_key, mainly songs that the
                        release credits to a TV show ("RAP VIỆT", "EM XINH \"SAY HI\""...) instead of
                        the people performing: performers first, in order, the show kept last and
                        marked as a programme (artist_type 'other', tag 'tv show').

apply_overrides() is idempotent and runs after loading (scripts/backfill.py). Entries whose artist
or track isn't in this database are skipped and counted, e.g. a teammate's DB without that data.
"""

import json
from datetime import date
from pathlib import Path

from psycopg2.extras import RealDictCursor

from music_project.db.artists import DEMOGRAPHIC_FIELDS, set_artist_demographics
from music_project.db.languages import classify_languages

OVERRIDES_DIR = Path(__file__).resolve().parents[3] / "overrides"


def _artist_id(cur, name: str, create: bool = False):
    if create:
        cur.execute("INSERT INTO artists (name) VALUES (%s) ON CONFLICT DO NOTHING", (name,))
    cur.execute("SELECT id FROM artists WHERE lower(name) = lower(%s)", (name,))
    row = cur.fetchone()
    return row[0] if row else None


def export_artists(conn, path: Path) -> int:
    """Writes every 'web' / 'manual' artist to path; returns how many."""
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"""SELECT name, match_status, demographics_source, {', '.join(DEMOGRAPHIC_FIELDS)}
                        FROM artists WHERE match_status IN ('web', 'manual') ORDER BY lower(name)""")
        rows = cur.fetchall()
    out = [{"name": r["name"], "status": r["match_status"], "source": r["demographics_source"],
            "fields": {f: r[f].isoformat() if isinstance(r[f], date) else r[f] for f in DEMOGRAPHIC_FIELDS}}
           for r in rows]
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return len(out)


def apply_credits(conn, path: Path) -> tuple:
    """Rewrites the credits of each listed track; returns (applied, missing tracks)."""
    applied = missing = 0
    for fix in json.loads(path.read_text(encoding="utf-8")):
        with conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM tracks WHERE match_key = %s", (fix["match_key"],))
            row = cur.fetchone()
            if row is None:
                missing += 1
                continue
            track_id = row[0]
            names = list(dict.fromkeys([*fix["performers"], fix["program"]]))
            ids = [_artist_id(cur, n, create=True) for n in names]
            cur.execute("DELETE FROM track_artists WHERE track_id = %s", (track_id,))
            for pos, artist_id in enumerate(dict.fromkeys(ids)):
                cur.execute("INSERT INTO track_artists (track_id, artist_id, position) VALUES (%s, %s, %s)",
                            (track_id, artist_id, pos))
            cur.execute("""UPDATE artists SET artist_type = 'other', match_status = 'web', demographics_source = 'web',
                                  tags = CASE WHEN 'tv show' = ANY(tags) THEN tags ELSE tags || '{tv show}'::text[] END,
                                  fetched_at = coalesce(fetched_at, now())
                           WHERE id = %s""", (ids[-1],))
        applied += 1
    return applied, missing


def apply_artists(conn, path: Path) -> tuple:
    """Sets each listed artist's facts; returns (applied, missing artists)."""
    applied = missing = 0
    for entry in json.loads(path.read_text(encoding="utf-8")):
        with conn, conn.cursor() as cur:
            artist_id = _artist_id(cur, entry["name"])
        if artist_id is None:
            missing += 1
            continue
        set_artist_demographics(conn, artist_id, entry["source"], status=entry["status"], **entry["fields"])
        applied += 1
    return applied, missing


def apply_overrides(conn, directory: Path = OVERRIDES_DIR) -> dict:
    """Applies credits.json then artists.json (if present), then refreshes language groups."""
    directory = Path(directory)
    stats = {"credits": 0, "credits_missing": 0, "artists": 0, "artists_missing": 0}
    if (directory / "credits.json").exists():
        stats["credits"], stats["credits_missing"] = apply_credits(conn, directory / "credits.json")
    if (directory / "artists.json").exists():
        stats["artists"], stats["artists_missing"] = apply_artists(conn, directory / "artists.json")
    classify_languages(conn)
    return stats
