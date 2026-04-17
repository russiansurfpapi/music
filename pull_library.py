"""Pull the full Spotify library into library.db.

Sources collected (each track can have multiple):
  - liked            (Saved Tracks / "Your Library")
  - lets_groove      (the "Let's Groove" playlist, flagged first-class)
  - playlist:<name>  (every other playlist you own or follow)
  - recent           (last 50 recently-played)

Run:
    python3 pull_library.py
    python3 pull_library.py --skip-playlists   # liked + recent only
"""

import argparse
import time
from typing import Iterable, Optional

import requests
import spotipy
from spotipy.exceptions import SpotifyException

from auth import get_spotify
from db import connect, init

PAGE = 50
SLEEP = 0.6  # ~50 requests per 30s dev-mode ceiling


class RateLimited(Exception):
    """Raised on 429. Caller should commit and bail — zero retries."""


def _guard(call, *args, **kwargs):
    """Run a spotipy call. 429 → RateLimited (no retry). Network timeouts → retry 3x."""
    transient = (requests.exceptions.ReadTimeout,
                 requests.exceptions.ConnectionError,
                 requests.exceptions.ChunkedEncodingError)
    for attempt in range(3):
        try:
            return call(*args, **kwargs)
        except SpotifyException as e:
            if getattr(e, "http_status", None) == 429:
                retry_after = e.headers.get("Retry-After") if getattr(e, "headers", None) else "?"
                raise RateLimited(f"429 — Retry-After: {retry_after}s") from e
            raise
        except transient as e:
            if attempt == 2:
                raise
            wait = 2 ** attempt
            print(f"    transient error ({type(e).__name__}); retrying in {wait}s")
            time.sleep(wait)


def _paged(call, *, items_key: str = "items", **kwargs) -> Iterable[dict]:
    """Generic pagination over a spotipy method that accepts limit/offset."""
    offset = 0
    while True:
        resp = _guard(call, limit=PAGE, offset=offset, **kwargs)
        items = resp.get(items_key, [])
        if not items:
            return
        for it in items:
            yield it
        if len(items) < PAGE or resp.get("next") is None:
            return
        offset += PAGE
        time.sleep(SLEEP)


def _upsert_track(conn, t: dict, source: str, added_at: Optional[str] = None) -> None:
    """Insert/update a track row and record the source."""
    if not t or not t.get("id"):
        return
    artists = t.get("artists") or []
    artist_name = ", ".join(a["name"] for a in artists) if artists else ""
    artist_id = artists[0]["id"] if artists else None
    album = (t.get("album") or {}).get("name")
    release = (t.get("album") or {}).get("release_date") or ""
    year = int(release[:4]) if release[:4].isdigit() else None

    conn.execute(
        """
        INSERT INTO tracks (spotify_id, title, artist, artist_id, album, release_year,
                            duration_ms, added_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(spotify_id) DO UPDATE SET
            title=excluded.title,
            artist=excluded.artist,
            artist_id=COALESCE(excluded.artist_id, tracks.artist_id),
            album=excluded.album,
            release_year=COALESCE(excluded.release_year, tracks.release_year),
            duration_ms=excluded.duration_ms,
            added_at=COALESCE(tracks.added_at, excluded.added_at),
            updated_at=CURRENT_TIMESTAMP
        """,
        (t["id"], t.get("name", ""), artist_name, artist_id, album, year,
         t.get("duration_ms"), added_at),
    )
    conn.execute(
        "INSERT OR IGNORE INTO track_sources (spotify_id, source) VALUES (?, ?)",
        (t["id"], source),
    )


def pull_liked(sp: spotipy.Spotify, conn) -> int:
    n = 0
    for item in _paged(sp.current_user_saved_tracks):
        _upsert_track(conn, item.get("track"), "liked", item.get("added_at"))
        n += 1
    conn.commit()
    print(f"  liked: {n}")
    return n


def pull_recent(sp: spotipy.Spotify, conn) -> int:
    resp = _guard(sp.current_user_recently_played, limit=50)
    n = 0
    for item in resp.get("items", []):
        _upsert_track(conn, item.get("track"), "recent", item.get("played_at"))
        n += 1
    conn.commit()
    print(f"  recent: {n}")
    return n


def pull_playlists(sp: spotipy.Spotify, conn) -> int:
    me = sp.current_user()["id"]
    total_tracks = 0
    playlists = list(_paged(sp.current_user_playlists))
    print(f"  found {len(playlists)} playlists")

    # Resume: skip sources already present in DB
    done_sources = {
        r[0] for r in conn.execute(
            "SELECT DISTINCT source FROM track_sources "
            "WHERE source LIKE 'playlist:%' OR source='lets_groove'"
        ).fetchall()
    }

    for pl in playlists:
        name = pl.get("name") or ""
        is_lets_groove = name.strip().lower() == "let's groove"
        source = "lets_groove" if is_lets_groove else f"playlist:{name}"
        owner = (pl.get("owner") or {}).get("id")
        # Skip foreign playlists with huge track counts to respect rate limits,
        # but always include ones you own + Let's Groove.
        if not is_lets_groove and owner != me and (pl.get("tracks") or {}).get("total", 0) > 500:
            continue
        if source in done_sources:
            print(f"    {name[:50]:50s} (already pulled — skip)")
            continue

        pl_id = pl["id"]
        count = 0
        offset = 0
        try:
            while True:
                resp = _guard(
                    sp.playlist_items,
                    pl_id,
                    limit=100,
                    offset=offset,
                    additional_types=("track",),
                    fields="items(added_at,track(id,name,duration_ms,artists(id,name),album(name,release_date))),next",
                )
                items = resp.get("items", [])
                if not items:
                    break
                for item in items:
                    _upsert_track(conn, item.get("track"), source, item.get("added_at"))
                    count += 1
                if resp.get("next") is None or len(items) < 100:
                    break
                offset += 100
                time.sleep(SLEEP)
        except SpotifyException as e:
            if getattr(e, "http_status", None) == 403:
                print(f"    {name[:50]:50s} (403 — skip)")
                continue
            raise
        conn.commit()
        tag = " ⭐" if is_lets_groove else ""
        print(f"    {name[:50]:50s} +{count}{tag}")
        total_tracks += count
    print(f"  playlist tracks: {total_tracks}")
    return total_tracks


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-liked", action="store_true")
    ap.add_argument("--skip-recent", action="store_true")
    ap.add_argument("--skip-playlists", action="store_true")
    args = ap.parse_args()

    init()
    sp = get_spotify()
    me = sp.current_user()
    print(f"Authed as: {me['display_name']} ({me['id']}) — {me.get('product', '?')}")

    with connect() as conn:
        try:
            if not args.skip_liked:
                print("Pulling liked...")
                pull_liked(sp, conn)

            if not args.skip_recent:
                print("Pulling recent plays...")
                pull_recent(sp, conn)

            if not args.skip_playlists:
                print("Pulling playlists...")
                pull_playlists(sp, conn)
        except RateLimited as e:
            conn.commit()
            print(f"\n⛔ Rate limited: {e}")
            print("Committed partial progress. Re-run later to resume (upserts are idempotent).")
            raise SystemExit(1)

        total = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        by_source = conn.execute(
            "SELECT source, COUNT(*) FROM track_sources GROUP BY source ORDER BY 2 DESC"
        ).fetchall()
        print(f"\nTotal unique tracks: {total}")
        print("By source:")
        for row in by_source:
            print(f"  {row[0]:40s} {row[1]}")


if __name__ == "__main__":
    main()
