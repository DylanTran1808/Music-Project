"""Split a Google Takeout YouTube watch history (HTML) into music vs. other videos.

Music = YouTube categoryId 10, looked up via the YouTube Data API v3
(50 ids per call, 1 quota unit each -> ~560 units for 28k videos; free quota is 10k/day).

Usage: YOUTUBE_API_KEY=... python scripts/yt_music_filter.py "<path to watch history .html>"
Writes data/yt_history.csv with an is_music column.
"""
import csv, html, json, os, re, sys, urllib.request
from pathlib import Path

ENTRY = re.compile(
    r'mdl-typography--title">(?P<product>.*?)<br>.*?'
    r'watch\?v=(?P<id>[\w-]{11})">(?P<title>.*?)</a><br>'
    r'(?:<a href="[^"]+">(?P<channel>.*?)</a><br>)?'
    r'(?P<time>[^<]+)<br>', re.S)
# "17:18:21 27 thg 9, 2026 CEST" -> 2026-09-27 17:18:21
VI_TIME = re.compile(r'(\d+:\d+:\d+) (\d+) thg (\d+), (\d+)')


def parse(path):
    for m in ENTRY.finditer(Path(path).read_text(encoding="utf-8")):
        t = VI_TIME.match(m["time"])
        yield {
            "video_id": m["id"],
            "title": html.unescape(m["title"]),
            "channel": html.unescape(m["channel"] or ""),
            "watched_at": f"{t[4]}-{int(t[3]):02}-{int(t[2]):02} {t[1]}" if t else m["time"],
            "product": m["product"],
        }


def categories(ids, key):
    ids, out = list(ids), {}
    for i in range(0, len(ids), 50):
        url = ("https://www.googleapis.com/youtube/v3/videos?part=snippet&maxResults=50"
               f"&id={','.join(ids[i:i + 50])}&key={key}")
        with urllib.request.urlopen(url) as r:
            for item in json.load(r)["items"]:
                out[item["id"]] = item["snippet"]["categoryId"]
        print(f"\r{min(i + 50, len(ids))}/{len(ids)}", end="", file=sys.stderr)
    return out  # deleted/private videos are missing


def is_music(row):
    return (row["category_id"] == "10"
            or row["channel"].endswith(" - Topic")
            or row["product"] != "YouTube")  # "YouTube Âm nhạc" = YouTube Music


if __name__ == "__main__":
    rows = list(parse(sys.argv[1]))
    cats = categories({r["video_id"] for r in rows}, os.environ["YOUTUBE_API_KEY"])
    for r in rows:
        r["category_id"] = cats.get(r["video_id"], "")
        r["is_music"] = is_music(r)
    out = Path(__file__).parent.parent / "data" / "yt_history.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    print(f"\n{sum(r['is_music'] for r in rows)}/{len(rows)} music -> {out}", file=sys.stderr)
