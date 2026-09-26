"""
Artist demographics in p_music: the MusicBrainz lookup loop and manual edits.

enrich_artists() looks up every artist not looked up yet, most-listened first, and commits
after each one, so it can be stopped and resumed. Clear matches are applied ('auto');
unclear ones keep their MusicBrainz + Wikidata candidates ('ambiguous' / 'not_found') for
the user, whose choice is saved with set_artist_demographics() as 'manual' and never
overwritten. Network failures leave the artist unlooked-up, to be retried next run.
"""

from typing import Callable, Optional

from psycopg2 import sql
from psycopg2.extras import Json, RealDictCursor

from music_project.connectors import musicbrainz, wikidata

DEMOGRAPHIC_FIELDS = ("mb_id", "wikidata_id", "artist_type", "gender", "country", "area", "birth_area",
                      "begin_date", "end_date", "release_languages", "first_release_year", "tags")

# Plays per artist: Apple play counts + Spotify plays, through any track the artist is credited on.
PLAYS = """
    (SELECT ta.artist_id, sum(p.n) AS plays FROM track_artists ta
     JOIN (SELECT track_id, play_count AS n FROM library_items
           UNION ALL SELECT track_id, 1 FROM listening_events WHERE track_id IS NOT NULL) p
       ON p.track_id = ta.track_id
     GROUP BY ta.artist_id)"""

# Not looked up yet, most-listened first.
PENDING = f"""
    SELECT a.id, a.name FROM artists a LEFT JOIN {PLAYS} s ON s.artist_id = a.id
    WHERE a.fetched_at IS NULL AND a.match_status IS DISTINCT FROM 'manual'
    ORDER BY coalesce(s.plays, 0) DESC, a.name"""

ALBUMS = """
    SELECT DISTINCT x.album FROM track_artists ta
    JOIN (SELECT track_id, album FROM library_items
          UNION SELECT track_id, album FROM listening_events WHERE track_id IS NOT NULL) x
      ON x.track_id = ta.track_id
    WHERE ta.artist_id = %s AND x.album IS NOT NULL"""


def _update(cur, artist_id: int, fields: dict, keep_manual: bool = True) -> None:
    sets = sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(k)) for k in fields)
    guard = sql.SQL(" AND match_status IS DISTINCT FROM 'manual'") if keep_manual else sql.SQL("")
    cur.execute(sql.SQL("UPDATE artists SET {} WHERE id = %s").format(sets) + guard,
                [*fields.values(), artist_id])


def set_artist_demographics(conn, artist_id: int, source: str, **fields) -> None:
    """The user's decision for an artist (source: musicbrainz / wikidata / user); saved as 'manual'."""
    unknown = set(fields) - set(DEMOGRAPHIC_FIELDS)
    if unknown:
        raise ValueError(f"unknown artist fields: {sorted(unknown)}")
    with conn, conn.cursor() as cur:
        _update(cur, artist_id, {**fields, "match_status": "manual", "demographics_source": source},
                keep_manual=False)
        cur.execute("UPDATE artists SET fetched_at = coalesce(fetched_at, now()) WHERE id = %s", (artist_id,))


def enrich_artists(conn, limit: Optional[int] = None, progress: Optional[Callable] = None) -> dict:
    """Looks up pending artists; returns counts {auto, ambiguous, not_found, errors}."""
    with conn, conn.cursor() as cur:
        cur.execute(PENDING)
        pending = cur.fetchall()[:limit]
    stats = {"auto": 0, "ambiguous": 0, "not_found": 0, "errors": 0}
    for i, (artist_id, name) in enumerate(pending):
        releases = {}

        def releases_of(mb_id):
            if mb_id not in releases:
                releases[mb_id] = musicbrainz.artist_releases(mb_id)
            return releases[mb_id]

        try:
            with conn, conn.cursor() as cur:
                cur.execute(ALBUMS, (artist_id,))
                albums = [r[0] for r in cur.fetchall()]
            candidates = musicbrainz.search_artist(name)
            status, match = musicbrainz.pick_match(name, candidates, albums, releases_of)
            if status == "auto":
                fields = {**musicbrainz.artist_fields(match), **musicbrainz.release_summary(releases_of(match["id"]))}
                fields.update(match_status="auto", demographics_source="musicbrainz", candidates=None)
            else:
                fields = {"match_status": status, "candidates": Json({
                    "musicbrainz": [musicbrainz.candidate_summary(c) for c in candidates],
                    "wikidata": wikidata.search_wikidata(name)})}
        except OSError as err:  # network / HTTP errors (requests' exceptions are OSErrors): retry next run
            stats["errors"] += 1
            if progress:
                progress(i + 1, len(pending), name, f"error: {err}")
            continue
        with conn, conn.cursor() as cur:
            _update(cur, artist_id, fields)
            cur.execute("UPDATE artists SET fetched_at = now() WHERE id = %s AND match_status IS DISTINCT FROM 'manual'",
                        (artist_id,))
        stats[status] += 1
        if progress:
            progress(i + 1, len(pending), name, status)
    return stats


def coverage(conn) -> list:
    """Artists and their plays per match status ('pending' = not looked up yet)."""
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"""
            SELECT CASE WHEN a.match_status IS NULL AND a.fetched_at IS NULL THEN 'pending' ELSE a.match_status END
                       AS status,
                   count(*) AS artists, coalesce(sum(s.plays), 0)::bigint AS plays
            FROM artists a LEFT JOIN {PLAYS} s ON s.artist_id = a.id
            GROUP BY 1 ORDER BY 3 DESC""")
        return cur.fetchall()


def review_items(conn) -> list:
    """Artists waiting for the user's decision, most-listened first, with what we know about them."""
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"""
            SELECT a.id, a.name, a.match_status, coalesce(s.plays, 0)::bigint AS plays, a.candidates,
                   coalesce((SELECT array_agg(DISTINCT x.album ORDER BY x.album) FROM track_artists ta
                             JOIN (SELECT track_id, album FROM library_items
                                   UNION SELECT track_id, album FROM listening_events) x ON x.track_id = ta.track_id
                             WHERE ta.artist_id = a.id AND x.album IS NOT NULL), '{{}}') AS albums,
                   coalesce((SELECT array_agg(DISTINCT u.handle ORDER BY u.handle) FROM track_artists ta
                             JOIN (SELECT track_id, account_id FROM library_items
                                   UNION SELECT track_id, account_id FROM listening_events) x
                               ON x.track_id = ta.track_id
                             JOIN user_accounts ua ON ua.id = x.account_id JOIN users u ON u.id = ua.user_id
                             WHERE ta.artist_id = a.id), '{{}}') AS listeners
            FROM artists a LEFT JOIN {PLAYS} s ON s.artist_id = a.id
            WHERE a.match_status IN ('ambiguous', 'not_found')
            ORDER BY coalesce(s.plays, 0) DESC, a.name""")
        return cur.fetchall()
