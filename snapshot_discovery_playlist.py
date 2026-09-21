"""Snapshot the DJ Set Discoveries playlist's membership into library.db.

`build_subgenre_playlists.py --source playlist` reads the `playlist_snapshots`
table to decide which tracks are "discoveries". Re-run whenever the playlist
changes.
"""

from auth import get_spotify
from tracklist_scraper import playlist_existing_ids
from db import connect
from spotify_budget import guard

DISCOVERY_PLAYLIST_ID = "4lIwulyeCrzOPTkDoZFe1l"


@guard
def main():
    sp = get_spotify()
    ids = playlist_existing_ids(sp, DISCOVERY_PLAYLIST_ID)
    with connect() as conn:
        # Replace wholesale: a snapshot is a point-in-time membership list,
        # so a track removed from the playlist must disappear from it too.
        conn.execute("DELETE FROM playlist_snapshots WHERE playlist_id=?",
                     (DISCOVERY_PLAYLIST_ID,))
        conn.executemany(
            "INSERT INTO playlist_snapshots (playlist_id, spotify_id) VALUES (?,?)",
            [(DISCOVERY_PLAYLIST_ID, i) for i in sorted(ids)])
        conn.commit()
    print(f"{len(ids)} track IDs -> playlist_snapshots")


if __name__ == "__main__":
    main()
