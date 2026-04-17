"""Upsert DJ-set tracks into the main `tracks` table + fetch Spotify metadata.

For every spotify_id in dj_set_tracks not yet in tracks:
  - Bulk-fetch metadata via sp.tracks([ids]) in batches of 50
  - Insert into tracks with lastfm_status='pending'
  - Insert track_sources row with source='dj_set:<dj_slug>'
For every new artist_id seen:
  - Fetch artist genres via sp.artist(id) and insert into artist_genres

Rate limit safety:
  - Bulk batches of 50 (29 tracks calls for ~1450 tracks)
  - 3s between batches → ~7 calls / 30s, well under 50/30s limit
  - Artist calls happen after, ~300 unique artists at 2s each
  - Total wall time: ~15-20 min
  - Bails immediately on 429 (no retries)

Usage:
    python3 dj_tracks_upsert.py             # full run
    python3 dj_tracks_upsert.py --dry-run   # show what would be fetched
"""

import argparse
import sys
import time
from typing import List, Set

from spotipy.exceptions import SpotifyException

from auth import get_spotify
from db import connect

TRACK_PAUSE = 1.5         # individual track fetch — batch endpoint is 403 in dev mode
ARTIST_PAUSE = 1.5
COMMIT_EVERY = 25


class RateLimited(Exception):
    pass


def _guard(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except SpotifyException as e:
        if getattr(e, "http_status", None) == 429:
            retry_after = e.headers.get("Retry-After") if getattr(e, "headers", None) else "?"
            raise RateLimited(f"429 — Retry-After: {retry_after}s") from e
        raise


def _missing_track_ids(conn) -> List[str]:
    """spotify_ids that appear in dj_set_tracks but not in tracks."""
    rows = conn.execute("""
        SELECT DISTINCT dst.spotify_id
        FROM dj_set_tracks dst
        LEFT JOIN tracks t ON t.spotify_id = dst.spotify_id
        WHERE dst.spotify_id IS NOT NULL
          AND t.spotify_id IS NULL
    """).fetchall()
    return [r["spotify_id"] for r in rows]


def _id_to_dj_sources(conn) -> dict:
    """Map spotify_id → set of dj_set:<slug> source strings (one per DJ that played the track)."""
    rows = conn.execute("""
        SELECT DISTINCT dst.spotify_id, ds.dj_slug
        FROM dj_set_tracks dst
        JOIN dj_sets ds ON ds.set_id = dst.set_id
        WHERE dst.spotify_id IS NOT NULL
    """).fetchall()
    out: dict = {}
    for r in rows:
        out.setdefault(r["spotify_id"], set()).add(f"dj_set:{r['dj_slug']}")
    return out


def _upsert_track(conn, t: dict) -> None:
    """Insert a Spotify track dict into the tracks table."""
    if not t or not t.get("id"):
        return
    artists = t.get("artists") or []
    artist_name = ", ".join(a["name"] for a in artists) if artists else ""
    artist_id = artists[0]["id"] if artists else None
    album = (t.get("album") or {}).get("name")
    release = (t.get("album") or {}).get("release_date") or ""
    year = int(release[:4]) if release[:4].isdigit() else None

    conn.execute("""
        INSERT INTO tracks (spotify_id, title, artist, artist_id, album, release_year,
                            duration_ms, added_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(spotify_id) DO UPDATE SET
            title=excluded.title,
            artist=excluded.artist,
            artist_id=COALESCE(excluded.artist_id, tracks.artist_id),
            album=excluded.album,
            release_year=COALESCE(excluded.release_year, tracks.release_year),
            duration_ms=excluded.duration_ms,
            updated_at=CURRENT_TIMESTAMP
    """, (t["id"], t.get("name", ""), artist_name, artist_id, album, year,
          t.get("duration_ms")))


def _record_sources(conn, spotify_id: str, sources: Set[str]) -> None:
    # Skip if track row doesn't exist yet (FK requires tracks.spotify_id)
    exists = conn.execute(
        "SELECT 1 FROM tracks WHERE spotify_id = ?", (spotify_id,)
    ).fetchone()
    if not exists:
        return
    for src in sources:
        conn.execute(
            "INSERT OR IGNORE INTO track_sources (spotify_id, source) VALUES (?, ?)",
            (spotify_id, src),
        )


def fetch_track_metadata(sp, conn, missing_ids: List[str], id_sources: dict) -> int:
    """Fetch tracks one at a time (batch endpoint is 403 in dev mode)."""
    n_done = 0
    total = len(missing_ids)
    for i, tid in enumerate(missing_ids, start=1):
        try:
            t = _guard(sp.track, tid)
        except RateLimited as e:
            conn.commit()
            print(f"\n  RATE LIMITED at track {i}/{total}: {e}")
            print(f"  Saved {n_done} tracks before bailing.")
            return n_done
        except SpotifyException as e:
            # 404 / unavailable etc — skip
            print(f"  ! skip {tid}: {getattr(e, 'http_status', '?')}")
            continue
        if t and t.get("id"):
            _upsert_track(conn, t)
            _record_sources(conn, t["id"], id_sources.get(t["id"], set()))
            n_done += 1
        if n_done % COMMIT_EVERY == 0:
            conn.commit()
            print(f"  [{n_done}/{total}] tracks upserted")
        if i < total:
            time.sleep(TRACK_PAUSE)
    conn.commit()
    return n_done


def fetch_artist_genres(sp, conn) -> int:
    """For every artist_id in tracks not yet in artist_genres, fetch genres."""
    rows = conn.execute("""
        SELECT DISTINCT t.artist_id
        FROM tracks t
        LEFT JOIN artist_genres ag ON ag.artist_id = t.artist_id
        WHERE t.artist_id IS NOT NULL
          AND ag.artist_id IS NULL
    """).fetchall()
    artist_ids = [r["artist_id"] for r in rows]
    total = len(artist_ids)
    if not total:
        print("  No new artists need genre fetching.")
        return 0

    print(f"  Fetching genres for {total} new artists...")
    n_done = 0
    for aid in artist_ids:
        try:
            artist = _guard(sp.artist, aid)
        except RateLimited as e:
            conn.commit()
            print(f"\n  RATE LIMITED at artist {n_done}: {e}")
            print(f"  Saved {n_done} artists before bailing.")
            return n_done
        except Exception as e:
            # Skip 404s and other artist-specific errors
            print(f"  ! skip {aid}: {type(e).__name__}")
            continue
        for g in artist.get("genres") or []:
            conn.execute(
                "INSERT OR IGNORE INTO artist_genres (artist_id, genre) VALUES (?, ?)",
                (aid, g),
            )
        n_done += 1
        if n_done % 25 == 0:
            conn.commit()
            print(f"  [{n_done}/{total}] artists done")
        time.sleep(ARTIST_PAUSE)
    conn.commit()
    print(f"  All {n_done} artists done.")
    return n_done


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="show what would be fetched")
    parser.add_argument("--skip-artists", action="store_true",
                        help="only fetch track metadata, skip artist-genre pull")
    args = parser.parse_args()

    conn = connect()
    try:
        missing = _missing_track_ids(conn)
        id_sources = _id_to_dj_sources(conn)
        print(f"DJ-set tracks needing metadata: {len(missing)}")

        already_in = conn.execute("""
            SELECT COUNT(DISTINCT dst.spotify_id)
            FROM dj_set_tracks dst
            JOIN tracks t ON t.spotify_id = dst.spotify_id
        """).fetchone()[0]
        print(f"Already in `tracks` (library overlap or prior run): {already_in}")

        if args.dry_run:
            est_min = (len(missing) * (TRACK_PAUSE + 0.3)) / 60
            print(f"Estimated track-fetch wall time: {est_min:.1f} min")
            return

        if missing:
            print(f"\nFetching metadata for {len(missing)} new tracks...")
            n = fetch_track_metadata(get_spotify(), conn, missing, id_sources)
            print(f"\nTrack metadata fetched: {n}/{len(missing)}")

        # Also record dj_set sources for tracks that already exist (library overlap)
        # so the analysis layer can detect them.
        for sid, srcs in id_sources.items():
            _record_sources(conn, sid, srcs)
        conn.commit()

        if not args.skip_artists:
            print("\n--- Fetching artist genres ---")
            fetch_artist_genres(get_spotify(), conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
