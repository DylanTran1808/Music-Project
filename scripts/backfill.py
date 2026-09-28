"""
Load every export in data/ (laid out like the HF dataset) into p_music and check it.

    uv run python scripts/backfill.py                    # data/, marks files that are on HF as owned
    uv run python scripts/backfill.py --data-dir ~/hf-snapshot --offline

Safe to re-run: Apple loads replace the account's library, Spotify loads skip stored
plays, existing people keep their demographics, and the committed fixes in overrides/
(TV-show song credits, researched artist facts) are re-applied. Prints per-account totals against the
source files (exit 1 on any mismatch), shared tracks per pair of people, and songs
merged from more than two source ids (worth a look: over-merging shows up here).
"""

import argparse
import json
import plistlib
import sys
from itertools import combinations
from pathlib import Path

from music_project.analysis.spotify_insights import _PII
from music_project.connectors import upload
from music_project.connectors.common import DEFAULT_REPO_ID
from music_project.db import connect
from music_project.db.load import load_data_dir
from music_project.db.overrides import apply_overrides


def source_totals(source, path):
    """Counts straight from the files, independent of the loaders' parsing."""
    if source == "apple_music":
        with open(path, "rb") as f:
            tracks = plistlib.load(f).get("Tracks", {}).values()
        return {"items": len(tracks), "plays": sum(t.get("Play Count", 0) for t in tracks)}
    records = [r for f in sorted(Path(path).glob("*.json")) for r in json.loads(f.read_text(encoding="utf-8"))]
    distinct = {json.dumps({k: v for k, v in r.items() if k not in _PII}, sort_keys=True) for r in records}
    return {"events": len(distinct)}


def db_totals(cur, source, account_id):
    if source == "apple_music":
        cur.execute("SELECT count(*), coalesce(sum(play_count), 0) FROM library_items WHERE account_id = %s",
                    (account_id,))
        items, plays = cur.fetchone()
        return {"items": items, "plays": int(plays)}
    cur.execute("SELECT count(*) FROM listening_events WHERE account_id = %s", (account_id,))
    return {"events": cur.fetchone()[0]}


# Songs, not recordings: liking "Goth" and "Goth (Slowed + Reverb)" is the same taste.
PERSON_SONGS = """
    SELECT DISTINCT u.handle, t.song_key FROM users u JOIN user_accounts a ON a.user_id = u.id
    JOIN (SELECT account_id, track_id FROM library_items
          UNION SELECT account_id, track_id FROM listening_events WHERE track_id IS NOT NULL) x
      ON x.account_id = a.id
    JOIN tracks t ON t.id = x.track_id"""

MERGE_GROUPS = """
    WITH ids AS (SELECT track_id, 'apple:' || account_id || ':' || apple_persistent_id AS sid FROM library_items
                 UNION SELECT track_id, 'spotify:' || external_id FROM track_external_ids)
    SELECT t.match_key, count(DISTINCT sid) AS n FROM ids JOIN tracks t ON t.id = ids.track_id
    GROUP BY t.match_key HAVING count(DISTINCT sid) > 2 ORDER BY n DESC, t.match_key"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default="data", help="folder with raw_am/ and raw_spot/ (default: data)")
    ap.add_argument("--offline", action="store_true", help="don't list the HF dataset; no account gets ownership")
    args = ap.parse_args()

    hf_files = None
    if not args.offline:
        try:
            hf_files = upload._api().list_repo_files(DEFAULT_REPO_ID, repo_type="dataset")
        except Exception as err:
            print(f"warning: couldn't list {DEFAULT_REPO_ID} ({err}); HF ownership not recorded", file=sys.stderr)

    conn = connect()
    results = load_data_dir(conn, args.data_dir, hf_files)
    fixes = apply_overrides(conn)  # committed catalogue fixes: TV-show credits, researched artist facts

    ok = True
    print("== Totals: source files vs p_music")
    with conn, conn.cursor() as cur:
        for r in sorted(results, key=lambda r: (r["source"], r["username"])):
            src, got = source_totals(r["source"], r["path"]), db_totals(cur, r["source"], r["account_id"])
            ok &= src == got
            print(f"{'OK      ' if src == got else 'MISMATCH'} {r['source']:<11} {r['username']:<10} "
                  f"source {src}  db {got}  hf: {r['hf_path'] or 'not on HF'}")

        cur.execute("SELECT count(*), count(DISTINCT song_key) FROM tracks")
        print("\n== Catalogue: {} recordings of {} songs".format(*cur.fetchone()))

        print("\n== Shared songs per pair of people")
        cur.execute(PERSON_SONGS)
        songs = {}
        for handle, song_key in cur.fetchall():
            songs.setdefault(handle, set()).add(song_key)
        for a, b in combinations(sorted(songs), 2):
            print(f"{a:>10} ~ {b:<10} {len(songs[a] & songs[b]):>5} shared   "
                  f"(of {len(songs[a])} / {len(songs[b])})")

        cur.execute(MERGE_GROUPS)
        groups = cur.fetchall()
        print(f"\n== Recordings merged from > 2 source ids: {len(groups)} (top 15)")
        for key, n in groups[:15]:
            print(f"{n:>4}  {key}")
    conn.close()
    print(f"\n== Overrides applied: {fixes}")
    print("\nall totals match" if ok else "\nMISMATCH: see above")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
