"""
Look up artist demographics on MusicBrainz, then write the review list.

    uv run python scripts/enrich_artists.py               # every artist not looked up yet (~2 s each)
    uv run python scripts/enrich_artists.py --limit 20    # just the 20 most-listened pending ones
    uv run python scripts/enrich_artists.py --review-only # only rewrite the review list

Stop it any time (Ctrl-C) and run it again: finished artists are kept, the rest continue.
Artists without a clear match go to data/artist_review.md (gitignored) with their
MusicBrainz and Wikidata candidates, most-listened first, for a person to decide.
"""

import argparse
import sys
from pathlib import Path

from music_project.db import connect
from music_project.db.artists import coverage, enrich_artists, review_items

REVIEW = Path("data/artist_review.md")


def _cell(v):
    return "" if v in (None, "", []) else str(v).replace("|", "/").replace("\n", " ")


def write_review(items, path: Path) -> None:
    lines = ["# Artists to review", "",
             f"{len(items)} artists without a clear MusicBrainz match, most-listened first. For each, pick a "
             "MusicBrainz (MB) or Wikidata (WD) candidate, give values yourself, or skip.", ""]
    for n, it in enumerate(items, 1):
        c = it["candidates"] or {}
        lines += [f"## {n}. {it['name']}  ({it['match_status']}, {it['plays']} plays, artist id {it['id']})", "",
                  f"- Listened by: {', '.join(it['listeners']) or '-'}",
                  f"- Our albums: {'; '.join(it['albums'][:8]) or '-'}", ""]
        if c.get("musicbrainz"):
            lines += ["| MB | name | disambiguation | type | gender | country | born / formed | birthplace | score |",
                      "|---|---|---|---|---|---|---|---|---|"]
            lines += [f"| MB{i} | {_cell(m['name'])} | {_cell(m['disambiguation'])} | {_cell(m['artist_type'])} | "
                      f"{_cell(m['gender'])} | {_cell(m['country'])} | {_cell(m['begin_date'])} | "
                      f"{_cell(m['birth_area'])} | {_cell(m['score'])} |" for i, m in enumerate(c["musicbrainz"], 1)]
            lines.append("")
        if c.get("wikidata"):
            lines += ["| WD | label | description | gender | country | born / formed | birthplace |",
                      "|---|---|---|---|---|---|---|"]
            lines += [f"| WD{i} | {_cell(w['label'])} | {_cell(w['description'])} | {_cell(w['gender'])} | "
                      f"{_cell(w['country'])} | {_cell(w['birth_date'])} | {_cell(w['birth_place'])} |"
                      for i, w in enumerate(c["wikidata"], 1)]
            lines.append("")
        if not c.get("musicbrainz") and not c.get("wikidata"):
            lines += ["_No candidates on MusicBrainz or Wikidata._", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, help="look up at most this many artists")
    ap.add_argument("--review-only", action="store_true", help="skip lookups, only rewrite the review list")
    args = ap.parse_args()

    conn = connect()
    if not args.review_only:
        def progress(i, total, name, status):
            print(f"[{i}/{total}] {status:<9} {name}", flush=True)
        stats = enrich_artists(conn, limit=args.limit, progress=progress)
        print(f"\nthis run: {stats}")

    rows = coverage(conn)
    total_artists = sum(r["artists"] for r in rows) or 1
    total_plays = sum(r["plays"] for r in rows) or 1
    print("\n== Coverage (share of artists / share of plays)")
    for r in rows:
        print(f"{r['status']:<10} {r['artists']:>5} artists ({r['artists'] / total_artists:6.1%})   "
              f"{r['plays']:>7} plays ({r['plays'] / total_plays:6.1%})")

    items = review_items(conn)
    write_review(items, REVIEW)
    print(f"\n{len(items)} artists to review -> {REVIEW}")
    conn.close()


if __name__ == "__main__":
    sys.exit(main())
