"""One-time import of the JSON sidecars into library.db.

Pipeline state used to live in three files next to the DB, read and written by
six different scripts:

    spotify_cache.json          -> spotify_id_cache (spotify_id NOT NULL)
    spotify_search_misses.json  -> spotify_id_cache (spotify_id NULL)
    discovery_playlist_ids.json -> playlist_snapshots

That split meant a crash could leave the DB and the files disagreeing, and
nothing enforced a single writer. Everything now lives in library.db.

Idempotent — safe to re-run. The JSON files are left on disk untouched; delete
them once you are satisfied the import is correct.
"""

import json
import os

from db import connect, init

DISCOVERY_PLAYLIST_ID = "4lIwulyeCrzOPTkDoZFe1l"

FILES = {
    "cache": "spotify_cache.json",
    "misses": "spotify_search_misses.json",
    "snapshot": "discovery_playlist_ids.json",
}


def _load(path, default):
    if not os.path.exists(path):
        print(f"  {path}: not present, skipping")
        return default
    return json.load(open(path))


def main():
    init()
    with connect() as conn:
        cache = _load(FILES["cache"], {})
        conn.executemany(
            "INSERT INTO spotify_id_cache (cache_key, spotify_id) VALUES (?,?) "
            "ON CONFLICT(cache_key) DO UPDATE SET spotify_id=excluded.spotify_id "
            "WHERE excluded.spotify_id IS NOT NULL",
            list(cache.items()))
        print(f"  spotify_cache.json      -> {len(cache)} id mappings")

        misses = _load(FILES["misses"], [])
        conn.executemany(
            "INSERT OR IGNORE INTO spotify_id_cache (cache_key, spotify_id) "
            "VALUES (?, NULL)", [(m,) for m in misses])
        print(f"  spotify_search_misses   -> {len(misses)} known-empty searches")

        snap = _load(FILES["snapshot"], [])
        conn.executemany(
            "INSERT OR IGNORE INTO playlist_snapshots (playlist_id, spotify_id) "
            "VALUES (?,?)", [(DISCOVERY_PLAYLIST_ID, s) for s in snap])
        print(f"  discovery_playlist_ids  -> {len(snap)} playlist members")

        conn.commit()

        hits = conn.execute(
            "SELECT COUNT(*) FROM spotify_id_cache WHERE spotify_id IS NOT NULL"
        ).fetchone()[0]
        dead = conn.execute(
            "SELECT COUNT(*) FROM spotify_id_cache WHERE spotify_id IS NULL"
        ).fetchone()[0]
        snaps = conn.execute("SELECT COUNT(*) FROM playlist_snapshots").fetchone()[0]

    print(f"\nlibrary.db now holds {hits} id mappings, {dead} known misses, "
          f"{snaps} snapshot rows")


if __name__ == "__main__":
    main()
