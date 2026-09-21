"""Find real Spotify IDs for classified tracks that only have synthetic ones.

Tracks scraped from DJ sets sometimes never matched a Spotify track, so they
carry an `lfm:`/`fp:` id. Many of them ARE classified — they have a subgenre
and are ready for a playlist, but there is no ID to add. One search each fixes
that.

Spotify's quota is the scarce resource here (a runaway loop earned a 23-hour
QUOTA_EXCEEDED ban once), so this script is deliberately timid:

  * `--limit` caps the run; default is small. Batch it, don't blast it.
  * Every result is written straight to library.db, so a ban never loses work.
  * A 429 aborts immediately. It does not sleep, retry, or back off.
  * A 5xx is Spotify hiccuping, not throttling — retried up to 3 times.
  * Misses are recorded so re-runs never re-search a known dead end.

Usage:
    python3 resolve_synthetic_ids.py --limit 50 --dry-run
    python3 resolve_synthetic_ids.py --limit 200
"""

import argparse
import sqlite3
import time

from spotipy.exceptions import SpotifyException

from artist_normalize import primary_artist, clean_title
from tracklist_scraper import _is_same_track, _load_spotify_cache, _cache_key
from auth import get_spotify
from db import connect

DELAY = 0.5  # ~120 req/min — well under the limit, and we are not in a hurry


from spotify_budget import guard

def candidates(conn, cache):
    """Classified synthetic-ID tracks with no cache hit and no prior miss."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT t.spotify_id, t.artist, t.title, c.subgenre
        FROM tracks t
        JOIN classifications c ON c.spotify_id = t.spotify_id
        WHERE c.subgenre IS NOT NULL
          AND (t.spotify_id LIKE 'lfm:%' OR t.spotify_id LIKE 'fp:%')
          AND t.spotify_id IN (SELECT spotify_id FROM dj_set_tracks
                               WHERE spotify_id IS NOT NULL)
        ORDER BY t.artist, t.title
    """).fetchall()
    out = []
    for r in rows:
        key = _cache_key(r["artist"], r["title"])
        if key in cache or key in cache.misses:
            continue
        out.append((key, r["artist"], r["title"]))
    return out


@guard
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50,
                    help="max searches this run (default 50 — keep it small)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cache = _load_spotify_cache()

    with connect() as conn:
        todo = candidates(conn, cache)

    print(f"{len(todo)} unresolved; searching {min(args.limit, len(todo))} this run")
    if args.dry_run:
        for key, a, t in todo[:args.limit]:
            print(f"  would search: {primary_artist(a)} — {clean_title(t)}")
        return

    sp = get_spotify()
    hits = 0
    for i, (key, artist, title) in enumerate(todo[:args.limit], 1):
        a, t = primary_artist(artist) or artist, clean_title(title) or title
        time.sleep(DELAY)
        res = None
        for attempt in range(3):
            try:
                res = sp.search(q=f"{a} {t}", type="track", limit=1)
                break
            except SpotifyException as e:
                if e.http_status == 429:
                    wait = (e.headers or {}).get("Retry-After", "?")
                    print(f"\n429 from Spotify after {i - 1} searches "
                          f"(Retry-After={wait}s). Stopping — progress is saved. "
                          f"Re-run later to continue.")
                    return
                # 5xx is Spotify hiccuping, not us being throttled. Back off
                # briefly and retry rather than losing the rest of the run.
                if e.http_status and 500 <= e.http_status < 600 and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise
        if res is None:
            print(f"  giving up on {a} — {t} after repeated 5xx")
            continue

        items = ((res or {}).get("tracks") or {}).get("items") or []
        # Both branches write straight through to library.db, so a ban
        # mid-run can never lose a resolved ID or re-search a dead end.
        # Verify the hit is the track asked for. Taking items[0] blind is what
        # put 158 wrong IDs in this same cache; this script writes straight to
        # it, so an unverified hit here re-creates that corruption wholesale.
        match = next((it for it in items if _is_same_track(a, t, it)), None)
        if match and match.get("id"):
            cache[key] = match["id"]
            hits += 1
        else:
            cache.mark_miss(key)

        if i % 25 == 0:
            print(f"  [{i}/{min(args.limit, len(todo))}] {hits} resolved", flush=True)

    print(f"\nresolved {hits}/{min(args.limit, len(todo))}; "
          f"{len(todo) - min(args.limit, len(todo))} still unsearched")


if __name__ == "__main__":
    main()
