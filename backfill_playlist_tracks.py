"""Fetch metadata for tracks that are on a playlist but unknown to library.db.

`sync_playlists.py` records playlist membership from Spotify, so an ID lands in
`playlist_tracks` whether or not anything ever fetched its artist and title.
1,213 accumulated that way. With no metadata they cannot be tagged, so they
cannot be classified, so they are invisible to the subgenre builders and to the
Mongo catalogue — present on the playlists, absent from every view of them.

**This spends Spotify quota one request at a time, and that call pattern has
earned a ban here before.** `.claude/rules/spotify-api.md` records a 10.8-hour
Retry-After after roughly 220 combined calls, and `sp.tracks([...])` — the
batch endpoint that would fix it — returns 403 in dev mode. So:

- `--max` defaults to 300, not the whole backlog. Run it across several days.
- Pacing and the daily ceiling come from `spotify_guard`, which every request
  passes through; nothing here re-implements them.
- Every track is committed as it arrives, so a ban costs the next request, not
  the run.
- Re-running picks up whatever is still missing. There is no state to reset.

Metadata alone does not finish the job: a track still needs Last.fm tags and a
classification before it reaches a playlist. Both are free.

    python3 backfill_playlist_tracks.py --dry-run
    python3 backfill_playlist_tracks.py --max 300
    python3 lastfm_tags.py          # then tags   (free)
    python3 classify.py             # then genre  (free)

Usage:
    python3 backfill_playlist_tracks.py [--max N] [--match '%— Study'] [--dry-run]
"""

import argparse
import sys
import time

import spotipy

from auth import get_spotify
from db import connect
from pull_library import _upsert_track      # the one writer for `tracks`
from spotify_budget import guard, status

# Below the guard's own pacing floor this is redundant, but an explicit pause
# between *track* calls is what the ban notes single out, so it stays visible.
CALL_DELAY = 0.5


def missing_ids(conn, match="%— Study", limit=None):
    """Real Spotify IDs on a matching playlist with no row in `tracks`."""
    rows = conn.execute("""
        SELECT DISTINCT pt.spotify_id
        FROM playlist_tracks pt
        JOIN playlists p ON p.playlist_id = pt.playlist_id
        WHERE p.name LIKE ?
          AND pt.spotify_id NOT LIKE 'lfm:%'
          AND pt.spotify_id NOT LIKE 'fp:%'
          AND length(pt.spotify_id) = 22
          AND pt.spotify_id NOT IN (SELECT spotify_id FROM tracks)
        ORDER BY pt.spotify_id
    """, (match,)).fetchall()
    ids = [r[0] for r in rows]
    return ids[:limit] if limit else ids


@guard
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=300,
                    help="tracks to fetch this run (default 300 — deliberately "
                         "short of the backlog; see the ban notes)")
    ap.add_argument("--match", default="%— Study")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with connect() as conn:
        todo = missing_ids(conn, args.match)
    print(f"{len(todo)} tracks on '{args.match}' playlists have no metadata")
    if not todo:
        return
    batch = todo[:args.max]
    used, limit, cooldown = status()
    print(f"this run: {len(batch)}   budget today: {used}/{limit}"
          f"{'   COOLDOWN ' + cooldown if cooldown else ''}")
    if args.dry_run:
        print("(dry-run — no API calls)")
        for i in batch[:10]:
            print(f"  {i}")
        return

    sp = get_spotify()
    got = failed = 0
    for n, sid in enumerate(batch, 1):
        try:
            t = sp.track(sid)
        except spotipy.SpotifyException as e:
            if e.http_status == 429:
                # The guard records the cooldown; stop immediately rather than
                # spend another request confirming it.
                print(f"\n429 after {n - 1} tracks — stopping. "
                      f"{got} saved, re-run later.")
                break
            if e.http_status == 404:
                print(f"  [{n}] {sid} — gone from Spotify")
                failed += 1
                continue
            raise
        except Exception as e:
            print(f"  [{n}] {sid} — {type(e).__name__}: {e}")
            failed += 1
            continue
        with connect() as conn:
            # Committed per track: a ban costs the next request, not the run.
            _upsert_track(conn, t, "playlist_backfill")
            conn.commit()
        got += 1
        if got % 25 == 0:
            artists = ", ".join(a["name"] for a in t.get("artists", []))
            print(f"  [{n}/{len(batch)}] {got} saved — {artists[:30]} — {t.get('name','')[:34]}")
        time.sleep(CALL_DELAY)

    used, limit, _ = status()
    print(f"\nsaved {got}, failed {failed}. budget {used}/{limit}")
    left = len(todo) - got
    if left > 0:
        print(f"{left} still missing — re-run tomorrow, or raise --max knowingly.")
    print("next (both free): python3 lastfm_tags.py && python3 classify.py")


if __name__ == "__main__":
    main()
