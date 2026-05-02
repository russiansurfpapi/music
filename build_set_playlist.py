"""Build a Spotify playlist from a fingerprinted YouTube set.

Reads dj_set_tracks for the given set_id, searches Spotify for each
(cache-aware), creates/updates a playlist, adds tracks incrementally.

Rate-limit safe: 4.5s delay per search, 120s pause per 50-track batch,
saves cache every track, instant bail on 429.

Usage:
    python3 build_set_playlist.py --set <set_id> [--name "Playlist Name"]
    python3 build_set_playlist.py --set <set_id> --dry-run   # no API calls
    python3 build_set_playlist.py --all-fingerprinted         # one playlist per fp set
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time

# Reuse the tracklist_scraper helpers (proven under rate-limit pressure)
from tracklist_scraper import (
    _load_spotify_cache, _save_spotify_cache, _cache_key,
    find_spotify_track, _get_or_create_playlist, _add_tracks_to_playlist,
    SpotifyRateLimited, BATCH_SIZE, BATCH_PAUSE,
)
from auth import get_spotify
from db import connect

# Slightly more conservative than tracklist_scraper's 4s default
TRACK_DELAY = 4.5


def default_name(title: str, dj_slug: str) -> str:
    """Map a set title to a playlist name."""
    if title:
        # Strip "(complete show)" / "(ACR)" / "(merge shazam+ACR)" etc.
        cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()
        if cleaned:
            return cleaned[:100]
    return f"{dj_slug.replace('-', ' ').title()} — fingerprinted"


def fetch_set_tracks(conn, set_id: str):
    row = conn.execute(
        "SELECT title, dj_slug FROM dj_sets WHERE set_id = ?", (set_id,)
    ).fetchone()
    if not row:
        return None, []
    tracks = conn.execute(
        """SELECT position, raw_artist, raw_title, spotify_id
           FROM dj_set_tracks
           WHERE set_id = ? ORDER BY position""", (set_id,)
    ).fetchall()
    # Filter placeholders
    filtered = []
    for r in tracks:
        a = (r["raw_artist"] or "").strip().lower()
        t = (r["raw_title"] or "").strip()
        if a in {"id", "i.d.", "unknown", "?", ""} or not t:
            continue
        filtered.append((r["raw_artist"], r["raw_title"], r["spotify_id"]))
    return dict(row), filtered


def build_one(conn, sp, cache, set_id: str, custom_name: str = None,
              dry_run: bool = False):
    meta, tracks = fetch_set_tracks(conn, set_id)
    if meta is None:
        print(f"  no such set: {set_id}"); return False
    if not tracks:
        print(f"  no usable tracks in {set_id}"); return False

    name = custom_name or default_name(meta["title"] or "", meta["dj_slug"])
    print(f"\n▣ {name}")
    print(f"  {len(tracks)} tracks to process")

    # Pre-resolved tracks (have spotify_id from fingerprinting or prior resolve)
    pre_resolved = sum(1 for _, _, sid in tracks if sid)
    miss_keys = [
        _cache_key(a, t) for a, t, sid in tracks
        if not sid and _cache_key(a, t) not in cache
    ]
    print(f"  pre-resolved: {pre_resolved}, cache hits: {len(tracks) - pre_resolved - len(miss_keys)}, "
          f"searches needed: {len(miss_keys)}")
    if dry_run:
        print("  (dry-run — no API calls)")
        return True

    playlist_id, playlist_url = _get_or_create_playlist(sp, name)
    if not playlist_id:
        print(f"  FAILED to create playlist — bailing"); return False
    print(f"  → {playlist_url}")

    batch_ids = []
    found = 0; missed = 0
    for i, (artist, title, pre_sid) in enumerate(tracks, 1):
        tid = None
        if pre_sid:
            tid = pre_sid
        else:
            key = _cache_key(artist, title)
            is_cached = key in cache
            try:
                tid = find_spotify_track(sp, artist, title, cache)
            except SpotifyRateLimited as e:
                print(f"  ⚠ RATE LIMITED at track {i}: {e}")
                _save_spotify_cache(cache)
                if batch_ids:
                    _add_tracks_to_playlist(sp, playlist_id, batch_ids)
                    print(f"  saved {len(batch_ids)} to playlist before bailing")
                return False
            if not is_cached:
                time.sleep(TRACK_DELAY)
                _save_spotify_cache(cache)

        if tid and len(tid) == 22 and tid.isalnum():
            if tid not in batch_ids:
                batch_ids.append(tid)
                found += 1
                tag = "●" if pre_sid else "✓"
                print(f"  [{i:>3}/{len(tracks)}] {tag} {artist[:30]:<30} — {title[:40]}")
            else:
                print(f"  [{i:>3}/{len(tracks)}] ↻ {artist[:30]:<30} — {title[:40]} (replay)")
        else:
            missed += 1
            print(f"  [{i:>3}/{len(tracks)}] ✗ {artist[:30]:<30} — {title[:40]}")

        if len(batch_ids) >= BATCH_SIZE:
            _add_tracks_to_playlist(sp, playlist_id, batch_ids)
            print(f"  → batch added to playlist ({len(batch_ids)} tracks). pausing {BATCH_PAUSE}s…")
            batch_ids = []
            time.sleep(BATCH_PAUSE)

    if batch_ids:
        _add_tracks_to_playlist(sp, playlist_id, batch_ids)

    _save_spotify_cache(cache)
    print(f"  DONE  found {found}/{len(tracks)} ({found*100//max(len(tracks),1)}%). "
          f"{missed} not on Spotify.")
    print(f"  {playlist_url}")
    return True


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--set", dest="set_id")
    g.add_argument("--all-fingerprinted", action="store_true")
    ap.add_argument("--name", default=None, help="override playlist name")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cache = _load_spotify_cache()
    sp = None if args.dry_run else get_spotify()

    with connect() as conn:
        conn.row_factory = sqlite3.Row
        if args.set_id:
            build_one(conn, sp, cache, args.set_id, args.name, args.dry_run)
        else:
            ids = [r["set_id"] for r in conn.execute(
                "SELECT set_id FROM dj_sets WHERE youtube_url IS NOT NULL "
                "ORDER BY track_count DESC"  # bigger sets first (more cache warming)
            ).fetchall()]
            print(f"processing {len(ids)} fingerprinted sets, one at a time...")
            for sid in ids:
                ok = build_one(conn, sp, cache, sid, None, args.dry_run)
                if not ok:
                    print("bailing on remaining sets"); break
                print("\n=== 60s cooldown between playlists ===\n")
                time.sleep(60)


if __name__ == "__main__":
    main()
