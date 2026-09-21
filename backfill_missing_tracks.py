"""Insert Spotify-ID'd tracks that live in `dj_set_tracks` but never made it
into `tracks`.

`lastfm_tags.promote_orphans()` only rescues dj_set_tracks rows whose
`spotify_id IS NULL` (it mints a synthetic `fp:` id for them). Rows that
carry a *real* Spotify ID but have no matching `tracks` row are invisible to
it, so they never get Last.fm tags and never get classified — which means
they silently vanish from every subgenre playlist.

Metadata comes from the normalised `dj_set_tracks.artist` / `title`, which the
fingerprinter and tracklist scraper already populate cleanly. Nothing is
fetched from Spotify: the batch `/tracks` endpoint returns 403 on this app
(the same restriction that blocks audio-features), and the raw fields are
good enough for Last.fm lookup anyway.

Rows are inserted with `lastfm_status='pending'` so the normal
`lastfm_tags.py` -> `classify.py` pipeline picks them up.

Usage:
    python3 backfill_missing_tracks.py [--dry-run]
"""

import argparse

from db import connect


def find_missing(conn):
    """Distinct real-Spotify-ID tracks present in dj_set_tracks, absent from tracks.

    Picks the most frequently-seen (raw_artist, raw_title) per ID so a single
    odd fingerprint reading doesn't win over the consensus spelling.
    """
    rows = conn.execute("""
        SELECT d.spotify_id,
               COALESCE(d.artist, d.raw_artist) AS artist,
               COALESCE(d.title,  d.raw_title)  AS title,
               COUNT(*) AS n
        FROM dj_set_tracks d
        LEFT JOIN tracks t ON t.spotify_id = d.spotify_id
        WHERE d.spotify_id IS NOT NULL
          AND d.spotify_id NOT LIKE 'lfm:%'
          AND d.spotify_id NOT LIKE 'fp:%'
          AND LENGTH(d.spotify_id) = 22
          AND t.spotify_id IS NULL
          AND d.raw_artist IS NOT NULL AND TRIM(d.raw_artist) != ''
          AND d.raw_title  IS NOT NULL AND TRIM(d.raw_title)  != ''
        GROUP BY d.spotify_id, artist, title
        ORDER BY d.spotify_id, n DESC
    """).fetchall()

    best = {}
    for sid, artist, title, _n in rows:
        if sid not in best:                      # first row per id = highest n
            best[sid] = (artist.strip(), title.strip())
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with connect() as conn:
        missing = find_missing(conn)
        print(f"tracks in dj_set_tracks but missing from `tracks`: {len(missing)}")
        if not missing:
            return

        for sid, (artist, title) in list(missing.items())[:5]:
            print(f"  e.g. {sid}  {artist} — {title}")

        if args.dry_run:
            print("(dry-run) no rows written")
            return

        inserted = 0
        for sid, (artist, title) in missing.items():
            cur = conn.execute("""
                INSERT OR IGNORE INTO tracks
                  (spotify_id, title, artist, origin, lastfm_status, features_status)
                VALUES (?, ?, ?, 'dj_set', 'pending', 'unavailable')
            """, (sid, title, artist))
            inserted += cur.rowcount or 0
        conn.commit()

        print(f"\nDONE. inserted={inserted}")
        print("Next: python3 lastfm_tags.py && python3 classify.py")


if __name__ == "__main__":
    main()
