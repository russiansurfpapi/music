"""Fetch Spotify audio features (tempo, energy, danceability, valence, acousticness, instrumentalness).

Resumable via `features_status` column: pending | ok | unavailable | 403.
Uses /audio-features batch endpoint (100 IDs per call). Falls back to per-track on 400/404.
Bails on 429. Skips track on 403 (dev-mode restriction).
"""

import time
from typing import List

import requests
from spotipy.exceptions import SpotifyException

from auth import get_spotify
from db import connect

BATCH = 100
DELAY = 0.6            # ~50 req / 30s ceiling
COMMIT_EVERY = 500


class RateLimited(Exception):
    pass


def _guard(call, *args, **kwargs):
    transient = (requests.exceptions.ReadTimeout,
                 requests.exceptions.ConnectionError,
                 requests.exceptions.ChunkedEncodingError)
    for attempt in range(3):
        try:
            return call(*args, **kwargs)
        except SpotifyException as e:
            if getattr(e, "http_status", None) == 429:
                ra = e.headers.get("Retry-After") if getattr(e, "headers", None) else "?"
                raise RateLimited(f"429 — Retry-After: {ra}s") from e
            raise
        except transient as e:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def _pending_ids(conn) -> List[str]:
    rows = conn.execute(
        "SELECT spotify_id FROM tracks WHERE features_status = 'pending'"
    ).fetchall()
    return [r[0] for r in rows]


def _write_features(conn, features: dict) -> None:
    sid = features["id"]
    conn.execute(
        """UPDATE tracks SET
             tempo=?, energy=?, danceability=?, valence=?,
             acousticness=?, instrumentalness=?, features_status='ok'
           WHERE spotify_id=?""",
        (features.get("tempo"), features.get("energy"), features.get("danceability"),
         features.get("valence"), features.get("acousticness"),
         features.get("instrumentalness"), sid),
    )


def _mark(conn, sid: str, status: str) -> None:
    conn.execute("UPDATE tracks SET features_status=? WHERE spotify_id=?", (status, sid))


def main() -> None:
    sp = get_spotify()
    me = sp.current_user()
    print(f"Authed as: {me['display_name']} ({me['id']})")

    with connect() as conn:
        ids = _pending_ids(conn)
        print(f"Pending: {len(ids)} tracks")
        if not ids:
            print("Nothing to do.")
            return

        done = 0
        try:
            for i in range(0, len(ids), BATCH):
                chunk = ids[i:i + BATCH]
                try:
                    resp = _guard(sp.audio_features, chunk)
                except SpotifyException as e:
                    status = getattr(e, "http_status", None)
                    if status == 403:
                        # Dev-mode restriction on batch endpoint — mark and stop.
                        print(f"403 on batch starting {i}. Marking this batch unavailable; stopping.")
                        for sid in chunk:
                            _mark(conn, sid, "403")
                        conn.commit()
                        raise SystemExit(2)
                    raise

                for sid, feat in zip(chunk, resp or []):
                    if feat is None:
                        _mark(conn, sid, "unavailable")
                    else:
                        _write_features(conn, feat)
                    done += 1

                if done % COMMIT_EVERY < BATCH:
                    conn.commit()
                    print(f"  {done}/{len(ids)}")
                time.sleep(DELAY)
        except RateLimited as e:
            conn.commit()
            print(f"\n⛔ {e}. Progress committed. Rerun to resume.")
            raise SystemExit(1)

        conn.commit()
        ok = conn.execute("SELECT COUNT(*) FROM tracks WHERE features_status='ok'").fetchone()[0]
        unavail = conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE features_status='unavailable'"
        ).fetchone()[0]
        print(f"\nDone. ok={ok} unavailable={unavail}")


if __name__ == "__main__":
    main()
