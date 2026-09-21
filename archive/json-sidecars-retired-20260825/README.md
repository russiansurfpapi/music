# Retired JSON sidecars (2026-08-25)

These held live pipeline state next to library.db, written by six different
scripts with no single owner. Imported into library.db by
`migrate_json_to_db.py` and no longer read by any code.

| file | now lives in |
|---|---|
| `spotify_cache.json` (3,990 ids) | `spotify_id_cache` where `spotify_id IS NOT NULL` |
| `spotify_search_misses.json` (48 total incl. 42 nulls from the cache) | `spotify_id_cache` where `spotify_id IS NULL` |
| `discovery_playlist_ids.json` (2,279) | `playlist_snapshots` |

Kept only as a rollback. Safe to delete.
