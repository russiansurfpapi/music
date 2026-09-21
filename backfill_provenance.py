"""Make every track traceable to where it came from.

Two gaps this closes:

1. 3,143 tracks had NO `track_sources` row at all, even though they are
   reachable through `dj_set_tracks`. The provenance existed; nobody wrote it.
2. `track_sources` recorded only `dj_set:<dj_slug>`, so a track played in eight
   different sets collapsed to one row and the set was lost. We now also write
   `dj_set:<slug>` per DJ AND rely on the `track_provenance` view for set-level
   detail (which set, what date, what position, how it was spelled there).

Idempotent — INSERT OR IGNORE throughout.
"""

from db import connect, init


def main():
    init()
    with connect() as conn:
        before_rows = conn.execute("SELECT COUNT(*) FROM track_sources").fetchone()[0]
        before_orphans = conn.execute(
            "SELECT COUNT(*) FROM tracks t WHERE NOT EXISTS "
            "(SELECT 1 FROM track_sources s WHERE s.spotify_id=t.spotify_id)"
        ).fetchone()[0]

        # Every (track, DJ) pair implied by the ingested sets.
        conn.execute("""
            INSERT OR IGNORE INTO track_sources (spotify_id, source)
            SELECT DISTINCT dst.spotify_id, 'dj_set:' || ds.dj_slug
            FROM dj_set_tracks dst
            JOIN dj_sets ds ON ds.set_id = dst.set_id
            WHERE dst.spotify_id IS NOT NULL
        """)
        conn.commit()

        after_rows = conn.execute("SELECT COUNT(*) FROM track_sources").fetchone()[0]
        after_orphans = conn.execute(
            "SELECT COUNT(*) FROM tracks t WHERE NOT EXISTS "
            "(SELECT 1 FROM track_sources s WHERE s.spotify_id=t.spotify_id)"
        ).fetchone()[0]

        print(f"track_sources rows   {before_rows} -> {after_rows} "
              f"(+{after_rows - before_rows})")
        print(f"tracks with no origin {before_orphans} -> {after_orphans}")

        if after_orphans:
            print("\nstill unattributed (no set, no source):")
            for r in conn.execute("""
                SELECT spotify_id, artist, title, origin FROM tracks t
                WHERE NOT EXISTS (SELECT 1 FROM track_sources s
                                  WHERE s.spotify_id=t.spotify_id) LIMIT 10"""):
                print(f"  {r['spotify_id'][:24]:24s} {(r['artist'] or '')[:24]:24s} "
                      f"{(r['title'] or '')[:24]:24s} origin={r['origin']}")


if __name__ == "__main__":
    main()
