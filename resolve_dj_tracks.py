"""Resolve unmatched dj_set_tracks → spotify_id.

Two stages:
  1. CACHE  — pull from existing spotify_cache.json (no API calls). Run first, always.
  2. SEARCH — for tracks still unresolved, hit Spotify API. ZERO retries, instant bail on 429.

Usage:
    python3 resolve_dj_tracks.py cache                # cache-only pass (free)
    python3 resolve_dj_tracks.py search [--max N]     # API pass, capped at N tracks
"""

import argparse
import json
import os
import sys
import time

from spotipy.exceptions import SpotifyException

from auth import get_spotify
from db import connect

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spotify_cache.json")
TRACK_DELAY = 5.0  # extra-conservative; user has hit bans recently


def _cache_key(artist: str, title: str) -> str:
    return f"{(artist or '').strip().lower()}||{(title or '').strip().lower()}"


def _load_cache():
    if not os.path.exists(CACHE_PATH):
        return {}
    with open(CACHE_PATH) as f:
        return json.load(f)


def _save_cache(cache):
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f)


def cmd_cache(args) -> None:
    cache = _load_cache()
    print(f"Cache has {len(cache)} entries ({sum(1 for v in cache.values() if v)} hits)")

    with connect() as conn:
        rows = conn.execute(
            "SELECT rowid, raw_artist, raw_title FROM dj_set_tracks "
            "WHERE spotify_id IS NULL AND raw_artist != 'spotify_uri'"
        ).fetchall()
        print(f"Unresolved rows: {len(rows)}")
        hit = 0
        for r in rows:
            key = _cache_key(r["raw_artist"], r["raw_title"])
            sid = cache.get(key)
            if sid:
                conn.execute(
                    "UPDATE dj_set_tracks SET spotify_id=? WHERE rowid=?",
                    (sid, r["rowid"]),
                )
                hit += 1
        # Also handle spotify_uri rows: extract ID from the URI in raw_title.
        uri_rows = conn.execute(
            "SELECT rowid, raw_title FROM dj_set_tracks "
            "WHERE spotify_id IS NULL AND raw_artist='spotify_uri'"
        ).fetchall()
        uri_hit = 0
        for r in uri_rows:
            t = (r["raw_title"] or "").strip()
            if t.startswith("spotify:track:"):
                sid = t.split(":")[-1]
                conn.execute(
                    "UPDATE dj_set_tracks SET spotify_id=? WHERE rowid=?",
                    (sid, r["rowid"]),
                )
                uri_hit += 1
        conn.commit()
        # Refresh per-set resolved counts.
        conn.execute("""
            UPDATE dj_sets SET resolved_count = (
                SELECT COUNT(*) FROM dj_set_tracks
                WHERE dj_set_tracks.set_id = dj_sets.set_id
                  AND dj_set_tracks.spotify_id IS NOT NULL
            )
        """)
        conn.commit()
        print(f"  cache hits: {hit}")
        print(f"  uri-extract hits: {uri_hit}")
        print(f"  total newly resolved: {hit + uri_hit}")


def _search_track(sp, artist: str, title: str):
    """Return spotify_id or None. Raise on 429 — let caller bail."""
    queries = [
        f"track:{title} artist:{artist}",
        f"{artist} {title}",
    ]
    for q in queries:
        resp = sp.search(q=q, type="track", limit=3)
        items = (resp.get("tracks") or {}).get("items", [])
        if items:
            # Prefer artist-name overlap.
            a = artist.lower().split(",")[0].strip()
            for it in items:
                names = " ".join(x["name"].lower() for x in it.get("artists", []))
                if a and a in names:
                    return it["id"]
            return items[0]["id"]
    return None


def cmd_search(args) -> None:
    cache = _load_cache()
    sp = get_spotify()
    me = sp.current_user()
    print(f"Authed: {me['display_name']}")

    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT raw_artist, raw_title FROM dj_set_tracks "
            "WHERE spotify_id IS NULL AND raw_artist != 'spotify_uri' "
            "AND length(raw_artist) > 1 AND length(raw_title) > 1"
        ).fetchall()
        if args.max:
            rows = rows[:args.max]
        print(f"Will search {len(rows)} unique (artist, title) pairs")
        print(f"Pacing: {TRACK_DELAY}s between calls. Will BAIL instantly on 429.")

        searched = 0
        found = 0
        for r in rows:
            key = _cache_key(r["raw_artist"], r["raw_title"])
            if key in cache:
                continue
            try:
                sid = _search_track(sp, r["raw_artist"], r["raw_title"])
            except SpotifyException as e:
                if getattr(e, "http_status", None) == 429:
                    ra = e.headers.get("Retry-After") if getattr(e, "headers", None) else "?"
                    print(f"\n⛔ 429 after {searched} searches (Retry-After: {ra}s). Saving cache, bailing.")
                    _save_cache(cache)
                    return
                print(f"  err on {r['raw_artist']} - {r['raw_title']}: {e}")
                sid = None
            cache[key] = sid
            if sid:
                found += 1
                conn.execute(
                    "UPDATE dj_set_tracks SET spotify_id=? "
                    "WHERE raw_artist=? AND raw_title=? AND spotify_id IS NULL",
                    (sid, r["raw_artist"], r["raw_title"]),
                )
            searched += 1
            if searched % 10 == 0:
                conn.commit()
                _save_cache(cache)
                print(f"  [{searched}/{len(rows)}] found {found} so far")
            time.sleep(TRACK_DELAY)

        conn.commit()
        _save_cache(cache)
        # Refresh per-set counts.
        conn.execute("""
            UPDATE dj_sets SET resolved_count = (
                SELECT COUNT(*) FROM dj_set_tracks
                WHERE dj_set_tracks.set_id = dj_sets.set_id
                  AND dj_set_tracks.spotify_id IS NOT NULL
            )
        """)
        conn.commit()
        print(f"\nDone. Searched {searched}, found {found}.")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("cache")
    s = sub.add_parser("search")
    s.add_argument("--max", type=int, default=0, help="Cap searches per run (default: all)")
    args = ap.parse_args()
    if args.cmd == "cache":
        cmd_cache(args)
    else:
        cmd_search(args)


if __name__ == "__main__":
    main()
