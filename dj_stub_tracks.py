"""Stub `tracks` rows for dj_set_tracks that don't have full Spotify metadata yet.

This lets the existing lastfm_tags.py + classify.py pipeline pick them up via
lastfm_status='pending', without needing further Spotify API calls.

Stub rows have:
  - spotify_id (from dj_set_tracks)
  - artist     (from raw_artist)
  - title      (from raw_title)
  - everything else NULL
  - lastfm_status='pending'

Idempotent: ON CONFLICT DO NOTHING — won't overwrite real metadata if it's
already there from dj_tracks_upsert or pull_library.

Usage:
    python3 dj_stub_tracks.py
"""

from db import connect


def main():
    conn = connect()
    try:
        # Find unique (spotify_id, raw_artist, raw_title) NOT in tracks
        rows = conn.execute("""
            SELECT DISTINCT dst.spotify_id, dst.raw_artist, dst.raw_title
            FROM dj_set_tracks dst
            LEFT JOIN tracks t ON t.spotify_id = dst.spotify_id
            WHERE dst.spotify_id IS NOT NULL
              AND t.spotify_id IS NULL
        """).fetchall()
        print(f"Stubbing {len(rows)} tracks...")

        n_added = 0
        for r in rows:
            cur = conn.execute("""
                INSERT INTO tracks (spotify_id, title, artist, lastfm_status)
                VALUES (?, ?, ?, 'pending')
                ON CONFLICT(spotify_id) DO NOTHING
            """, (r["spotify_id"], r["raw_title"], r["raw_artist"]))
            if cur.rowcount:
                n_added += 1
                # Also record dj_set source(s)
                src_rows = conn.execute("""
                    SELECT DISTINCT ds.dj_slug
                    FROM dj_set_tracks dst
                    JOIN dj_sets ds ON ds.set_id = dst.set_id
                    WHERE dst.spotify_id = ?
                """, (r["spotify_id"],)).fetchall()
                for sr in src_rows:
                    conn.execute(
                        "INSERT OR IGNORE INTO track_sources (spotify_id, source) VALUES (?, ?)",
                        (r["spotify_id"], f"dj_set:{sr['dj_slug']}"),
                    )
        conn.commit()
        print(f"Added {n_added} stub rows.")

        # Sanity check
        pending = conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE lastfm_status='pending'"
        ).fetchone()[0]
        print(f"Tracks now pending Last.fm: {pending}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
