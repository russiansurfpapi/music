"""Build per-DJ 'study' playlists — one playlist per DJ combining ALL their
tracks across both 1001Tracklists-scraped sets and YouTube-fingerprinted sets.

Named "<DJ Display Name> — Study" so they stay SEPARATE from any existing
scraped playlists ("<DJ> Sets" etc.) that live in the user's library.

Uses the library.db ID cache only — zero new searches, near-zero rate-limit risk.
Run AFTER the fingerprint playlists are built so the cache is fully warm.

Dedupe by (artist, title) before resolving, and by Spotify ID before adding.

Usage:
    python3 build_dj_study_playlists.py                    # all DJs with ≥1 fingerprinted set
    python3 build_dj_study_playlists.py --dj jamie-xx      # one DJ
    python3 build_dj_study_playlists.py --include-1001tl-only  # also do DJs with only 1001TL data
    python3 build_dj_study_playlists.py --dry-run          # preview only
"""

import argparse
import json
import sqlite3
import time
from collections import defaultdict

from tracklist_scraper import (
    _load_spotify_cache, _cache_key,
    _get_or_create_playlist, _add_tracks_to_playlist,
)
from auth import get_spotify
from db import connect


def gather_all_tracks(conn, include_1001tl_only=False):
    """Return {dj_slug: [(artist, title, source_tag), ...]} combining every
    track across both 1001TL sets and fingerprinted sets for each DJ.

    By default, only includes DJs with at least one fingerprinted set
    (that's what this session is focused on). Pass include_1001tl_only=True
    to cover every DJ in the DB.
    """
    eligible_djs = [r["dj_slug"] for r in conn.execute(
        "SELECT DISTINCT dj_slug FROM dj_sets WHERE youtube_url IS NOT NULL"
    ).fetchall()]
    dj_filter = "" if include_1001tl_only else \
                f"AND s.dj_slug IN ({','.join('?'*len(eligible_djs))})"
    params = () if include_1001tl_only else tuple(eligible_djs)

    rows = conn.execute(f"""
        SELECT s.dj_slug, t.raw_artist, t.raw_title,
               CASE WHEN s.youtube_url IS NOT NULL THEN 'fingerprint'
                    ELSE '1001tl' END AS origin
        FROM dj_set_tracks t
        JOIN dj_sets s ON s.set_id = t.set_id
        WHERE LOWER(TRIM(t.raw_artist)) NOT IN ('id','i.d.','unknown','?','')
          AND TRIM(t.raw_title) <> ''
          {dj_filter}
        ORDER BY s.dj_slug, s.set_date, t.position
    """, params).fetchall()
    by_dj = defaultdict(list)
    for r in rows:
        by_dj[r["dj_slug"]].append((r["raw_artist"], r["raw_title"], r["origin"]))
    return by_dj


def dj_display_name(conn, slug):
    row = conn.execute("SELECT name FROM djs WHERE slug=?", (slug,)).fetchone()
    return row["name"] if row else slug.replace("-", " ").title()


def build_for_dj(sp, cache, conn, dj_slug, tracks, dry_run=False):
    name = dj_display_name(conn, dj_slug)
    playlist_name = f"{name} — Study"

    # Dedupe (artist, title) pairs preserving order; resolve via cache
    seen = set()
    track_ids = []
    id_seen = set()      # second dedupe on spotify_id (different text, same track)
    missing = 0
    fp_count = 0; tl_count = 0
    for artist, title, origin in tracks:
        key = _cache_key(artist, title)
        if key in seen: continue
        seen.add(key)
        tid = cache.get(key)
        if tid and tid not in id_seen:
            track_ids.append(tid)
            id_seen.add(tid)
            if origin == "fingerprint": fp_count += 1
            else: tl_count += 1
        elif not tid:
            missing += 1

    print(f"\n▣ {playlist_name}")
    print(f"  total (artist, title) rows: {len(tracks)}")
    print(f"  unique pairs:               {len(seen)}")
    print(f"  resolved unique Spotify IDs:{len(track_ids)}")
    print(f"    from fingerprint:         {fp_count}")
    print(f"    from 1001tl:              {tl_count}")
    print(f"  not in cache / Spotify:     {missing}")

    if dry_run:
        print("  (dry-run)"); return

    if not track_ids:
        print("  no cached track IDs — skipping"); return

    pid, url = _get_or_create_playlist(sp, playlist_name)
    if not pid:
        print("  FAILED to create/find playlist"); return
    print(f"  → {url}")
    _add_tracks_to_playlist(sp, pid, track_ids)
    print(f"  DONE — {len(track_ids)} tracks attempted (Spotify dedupes across adds)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dj", default=None, help="one DJ slug only")
    ap.add_argument("--include-1001tl-only", action="store_true",
                    help="also build for DJs with no fingerprinted sets")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cache = _load_spotify_cache()
    sp = None if args.dry_run else get_spotify()

    with connect() as conn:
        conn.row_factory = sqlite3.Row
        by_dj = gather_all_tracks(conn, args.include_1001tl_only)
        targets = [args.dj] if args.dj else sorted(by_dj.keys())
        for dj in targets:
            if dj not in by_dj:
                print(f"no fingerprinted tracks for {dj}"); continue
            build_for_dj(sp, cache, conn, dj, by_dj[dj], args.dry_run)
            if len(targets) > 1 and not args.dry_run:
                # Small breather between playlists — playlist-modify is not the
                # rate-limited endpoint, but be gentle
                time.sleep(3)


if __name__ == "__main__":
    main()
