"""Enrich library.db tracks with Last.fm tags.

For each pending track:
  1. track.getTopTags (artist, track, autocorrect=1) — if hit, level='track'
  2. else artist.getTopTags (cached per artist) — if hit, level='artist'
  3. else status='missing'

Rate-limited to 5 req/s. Resumable. Commits every 50 tracks.
"""

import os
import sys
import time
import sqlite3
from typing import List, Optional, Tuple

import requests
from dotenv import load_dotenv

_HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_HERE, ".env"))

sys.path.insert(0, _HERE)
import db as dbmod  # noqa

LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")
LASTFM_BASE_URL = "https://ws.audioscrobbler.com/2.0/"

REQUEST_DELAY = 0.2  # 200ms => 5 req/s
COMMIT_EVERY = 50
LOG_EVERY = 100
MAX_RETRIES = 3


def _first_artist(artist_field: str) -> str:
    if not artist_field:
        return ""
    return artist_field.split(",")[0].strip()


def _lastfm_call(method: str, params: dict) -> Optional[dict]:
    """Call Last.fm with retries. Returns parsed JSON or None on persistent failure."""
    q = {
        "method": method,
        "api_key": LASTFM_API_KEY,
        "format": "json",
        "autocorrect": "1",
    }
    q.update(params)

    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(LASTFM_BASE_URL, params=q, timeout=15)
            if r.status_code == 200:
                try:
                    return r.json()
                except Exception:
                    return None
            # Last.fm returns 200 even for "no data"; non-200 likely transient/ban
            if r.status_code in (403, 429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            # Other 4xx — likely missing artist/track, return parsed body if possible
            try:
                return r.json()
            except Exception:
                return None
        except requests.RequestException:
            time.sleep(2 ** attempt)
            continue
    return None


def _extract_tags(data: Optional[dict], parent_key: str) -> List[Tuple[str, int]]:
    """Extract (tag, count) pairs from toptags response. parent_key='toptags'."""
    if not data or "error" in data:
        return []
    top = data.get(parent_key) or {}
    tag = top.get("tag")
    if not tag:
        return []
    if isinstance(tag, dict):
        tag = [tag]
    out = []
    for t in tag:
        name = (t.get("name") or "").strip()
        if not name:
            continue
        try:
            count = int(t.get("count", 0))
        except (ValueError, TypeError):
            count = 0
        out.append((name, count))
    return out


def get_track_tags(artist: str, track: str) -> Optional[List[Tuple[str, int]]]:
    data = _lastfm_call("track.getTopTags", {"artist": artist, "track": track})
    if data is None:
        return None  # transient failure
    return _extract_tags(data, "toptags")


def get_artist_tags(artist: str) -> Optional[List[Tuple[str, int]]]:
    data = _lastfm_call("artist.getTopTags", {"artist": artist})
    if data is None:
        return None
    return _extract_tags(data, "toptags")


def main():
    if not LASTFM_API_KEY:
        print("ERROR: LASTFM_API_KEY not set")
        sys.exit(1)

    conn = dbmod.connect()
    try:
        total = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        pending_count = conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE lastfm_status='pending'"
        ).fetchone()[0]
        done_count = total - pending_count
        print(f"Total tracks: {total}; pending: {pending_count}; already done: {done_count}")

        cur = conn.execute(
            "SELECT spotify_id, title, artist FROM tracks "
            "WHERE lastfm_status='pending' ORDER BY artist, title"
        )
        rows = cur.fetchall()

        artist_cache = {}  # first_artist lower -> List[(tag,count)] or []
        processed = 0
        stats = {"track": 0, "artist": 0, "missing": 0, "error": 0}
        last_req_time = 0.0

        def throttle():
            nonlocal last_req_time
            dt = time.time() - last_req_time
            if dt < REQUEST_DELAY:
                time.sleep(REQUEST_DELAY - dt)
            last_req_time = time.time()

        for row in rows:
            sid = row["spotify_id"]
            title = row["title"]
            artist_full = row["artist"]
            first = _first_artist(artist_full)
            processed += 1
            overall_idx = done_count + processed

            if not first or not title:
                conn.execute(
                    "UPDATE tracks SET lastfm_status='missing', updated_at=CURRENT_TIMESTAMP "
                    "WHERE spotify_id=?", (sid,)
                )
                stats["missing"] += 1
                if processed % COMMIT_EVERY == 0:
                    conn.commit()
                continue

            # --- track.getTopTags ---
            throttle()
            track_tags = get_track_tags(first, title)

            if track_tags is None:
                # transient failure — skip, leave pending
                stats["error"] += 1
                if processed % LOG_EVERY == 0:
                    print(f"[{overall_idx}/{total}] {first} — {title} -> network error (left pending)")
                if processed % COMMIT_EVERY == 0:
                    conn.commit()
                continue

            level = None
            tags_to_insert: List[Tuple[str, int]] = []

            if track_tags:
                level = "track"
                tags_to_insert = track_tags
            else:
                # --- artist.getTopTags (cached) ---
                cache_key = first.lower()
                if cache_key in artist_cache:
                    a_tags = artist_cache[cache_key]
                else:
                    throttle()
                    a_tags = get_artist_tags(first)
                    if a_tags is None:
                        stats["error"] += 1
                        if processed % LOG_EVERY == 0:
                            print(f"[{overall_idx}/{total}] {first} — {title} -> artist network error (left pending)")
                        if processed % COMMIT_EVERY == 0:
                            conn.commit()
                        continue
                    artist_cache[cache_key] = a_tags

                if a_tags:
                    level = "artist"
                    tags_to_insert = a_tags

            if level:
                for tag, count in tags_to_insert:
                    try:
                        conn.execute(
                            "INSERT OR IGNORE INTO track_tags (spotify_id, tag, count, level) "
                            "VALUES (?, ?, ?, ?)",
                            (sid, tag, count, level),
                        )
                    except sqlite3.Error:
                        pass
                new_status = level
            else:
                new_status = "missing"

            conn.execute(
                "UPDATE tracks SET lastfm_status=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE spotify_id=?", (new_status, sid)
            )
            stats[new_status] += 1

            if processed % LOG_EVERY == 0:
                n_tags = len(tags_to_insert)
                print(f"[{overall_idx}/{total}] {first} — {title} -> {n_tags} {new_status} tags "
                      f"(track:{stats['track']} artist:{stats['artist']} missing:{stats['missing']} err:{stats['error']})")

            if processed % COMMIT_EVERY == 0:
                conn.commit()

        conn.commit()
        print(f"\nDONE. Processed {processed} tracks.")
        print(f"  track-level: {stats['track']}")
        print(f"  artist-level: {stats['artist']}")
        print(f"  missing: {stats['missing']}")
        print(f"  left pending (errors): {stats['error']}")
    finally:
        conn.commit()
        conn.close()


if __name__ == "__main__":
    main()
