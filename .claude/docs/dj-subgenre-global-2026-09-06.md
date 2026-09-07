> **Archived 2026-09-06.** The `## DJ subgenre playlists` section of
> `~/.claude/rules/codebase-map.md`, which used to auto-load into every project
> on this machine (agent3a, FUM, ig_personality) — none of which contain any of
> this code. 52 of its 57 backticked tokens were already covered by this repo's
> own `.claude/rules/` (602 lines) plus `SUBGENRE_PLAYLISTS.md` (231 lines);
> the one substantive gap — `db.migrate()` / `_ADDED_COLUMNS` — was carried into
> `.claude/rules/codebase-map.md`. Kept whole here so nothing is lost.

## DJ subgenre playlists (~/Documents/music)
- `build_subgenre_playlists.py --min-tracks 10 --sync` — builds 32 `<Subgenre> — Study` Spotify playlists
- `--source dj-sets` (DEFAULT, correct) = library.db `dj_set_tracks`; `playlist` = Spotify snapshot (lossy); `library` = whole library (WRONG — bloats playlists)
- `--sync` removes tracks that no longer belong; without it adds only
- `snapshot_discovery_playlist.py` — refreshes `discovery_playlist_ids.json` for `--source playlist`
- `backfill_missing_tracks.py` — rescues dj_set_tracks rows with real Spotify IDs but no `tracks` row (promote_orphans only handles `spotify_id IS NULL`)
- `--level genre` builds `<Genre> (Other) — Study` catch-alls for tracks stuck on an umbrella tag
- `refetch_missing_tags.py` — retries Last.fm with normalised artist spellings (~40% recovery, no Spotify cost)
- `resolve_synthetic_ids.py --limit N` — Spotify-searches classified tracks that lack a real ID (~98% hit). SPENDS QUOTA: batch it, it aborts on 429 without losing work
- `artist_normalize.py` — scraper emits `Disclosureft. Eliza Doolittle`; Last.fm also fails on diacritics (`RÜFÜS DU SOL` -> 0 tags, `Rufus Du Sol` -> ok)
- `spotify_budget.py` — HARD daily ceiling (default 2500, `SPOTIFY_DAILY_LIMIT` to override). Every call goes through `_spotify_call` -> `charge()`. `python3 spotify_budget.py` shows spend + cooldown
- A long Retry-After writes `cooldown_until` to `spotify_api_usage`; later runs refuse BEFORE calling, so re-running during a ban costs 0 requests
- `playlists.snapshot_id` changes iff contents changed — membership reads are skipped when it matches. Playlist index comes from the `playlists` table, not 17 API pages
- `spotify_guard.py` patches `spotipy.Spotify._internal_call` (the true HTTP chokepoint) — importing it governs EVERY client, incl. the ~90 direct `sp.foo()` calls. `check_budget_guard.py` fails if a script imports spotipy without it
- NEVER paginate on `len(items) < page_size` — Spotify emits 49-item pages mid-listing. Always use `next`. This truncated the catalogue to 547 of 1,546 playlists
- Measured: sync_playlists 287 -> 11 requests; build --sync ~130 -> 12
- `library.db` is the SINGLE source of truth — `spotify_id_cache` (NULL id = known-miss) and `playlist_snapshots` replaced spotify_cache.json / spotify_search_misses.json / discovery_playlist_ids.json
- `_load_spotify_cache()` returns a dict-like that writes through to the DB per key; `_save_spotify_cache()` is a no-op kept for compatibility
- `dj_set_tracks` has BOTH `raw_artist`/`raw_title` (as scraped, never rewritten) and `artist`/`title` (normalised at ingest). 46% of rows differ
- `db.migrate()` handles ALTER TABLE ADD COLUMN idempotently; add to `_ADDED_COLUMNS`
- `backfill_normalized_names.py [--force]` backfills the normalised columns
- `playlists` + `playlist_tracks` = which tracks are on which playlist; `sync_playlists.py` refreshes (catalogue cheap, membership expensive — defaults to managed only)
- `track_provenance` VIEW = one row per (track, origin) with dj/set/date/position/scraped-as. `track_sources` collapses to one row per DJ, the view does not
- `track_story.py "<query>"` — everything known about a track: metadata, every set it appeared in, every playlist it is on
- `backfill_provenance.py` — rebuilds track_sources from dj_set_tracks (recovered 3,143 unattributed tracks)
- Spotify moved playlist track_count from `tracks.total` to `items.total` (same shape change as playlist items)
- Full notes + API gotchas: `~/Documents/music/SUBGENRE_PLAYLISTS.md`
