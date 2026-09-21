"""Retry Last.fm for tracks marked `missing`, using normalised artist spellings.

The original fetch queried the raw scraped artist string. That string is often
unusable — features are concatenated ("Disclosureft. Eliza Doolittle") and
diacritics are not autocorrected by Last.fm ("RÜFÜS DU SOL" -> 0 tags).
`artist_normalize.artist_candidates()` supplies looser spellings to try.

Measured recovery on a 120-track sample: 43%.

Usage:
    python3 refetch_missing_tags.py                # DJ-set tracks only (default)
    python3 refetch_missing_tags.py --all          # every 'missing' track
    python3 refetch_missing_tags.py --limit 200    # small batch
"""

import argparse
import sqlite3
import time

from artist_normalize import artist_candidates, clean_title
from lastfm_tags import get_track_tags, get_artist_tags
from db import connect

DELAY = 0.2  # Last.fm allows ~5 req/s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true",
                    help="every 'missing' track, not just DJ-set ones")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    scope = "" if args.all else (
        " AND t.spotify_id IN "
        "(SELECT spotify_id FROM dj_set_tracks WHERE spotify_id IS NOT NULL)")
    lim = f" LIMIT {args.limit}" if args.limit else ""

    with connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT spotify_id, artist, title FROM tracks t "
            f"WHERE t.lastfm_status='missing'{scope} ORDER BY artist, title{lim}"
        ).fetchall()
        total = len(rows)
        print(f"retrying {total} tracks marked 'missing'")

        artist_cache = {}
        stats = {"track": 0, "artist": 0, "missing": 0}

        for i, r in enumerate(rows, 1):
            sid, raw_artist, title = r["spotify_id"], r["artist"], r["title"]
            cands = artist_candidates(raw_artist or "")
            titles = list(dict.fromkeys(t for t in (title, clean_title(title)) if t))
            found = None

            # Track-level first: more specific tags than the artist's.
            for a in cands:
                for t in titles:
                    time.sleep(DELAY)
                    tags = get_track_tags(a, t)
                    if tags:
                        found = ("track", tags)
                        break
                if found:
                    break

            if not found:
                for a in cands:
                    key = a.lower()
                    if key not in artist_cache:
                        time.sleep(DELAY)
                        artist_cache[key] = get_artist_tags(a) or []
                    if artist_cache[key]:
                        found = ("artist", artist_cache[key])
                        break

            if found:
                level, tags = found
                for tag, count in tags:
                    conn.execute(
                        "INSERT OR IGNORE INTO track_tags "
                        "(spotify_id, tag, count, level) VALUES (?,?,?,?)",
                        (sid, tag, count, level))
                status = level
            else:
                status = "missing"

            stats[status] += 1
            conn.execute(
                "UPDATE tracks SET lastfm_status=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE spotify_id=?", (status, sid))

            if i % 50 == 0:
                conn.commit()
                rec = stats["track"] + stats["artist"]
                print(f"  [{i}/{total}] recovered {rec} ({rec/i:.0%})", flush=True)

        conn.commit()

    rec = stats["track"] + stats["artist"]
    print(f"\ndone: {rec}/{total} recovered "
          f"(track-level {stats['track']}, artist-level {stats['artist']}), "
          f"{stats['missing']} still missing")


if __name__ == "__main__":
    main()
