# Genre DNA — Build Status (2026-04-13)

## Where we are
- ✅ Piece 1: scaffold (auth.py, db.py, library.db)
- 🟡 Piece 2: library pull — **15,012 tracks** in DB (14,989 liked + 43 recent). Playlists broken.
- ⏳ Piece 3: audio features + artist genres
- ⏳ Piece 4: Last.fm tag enrichment
- ⏳ Piece 5: 6-layer tag mapper
- ⏳ Piece 6: summary report

## Open issues

### Playlist pull (piece 2)
- 599 playlists exist. First run got 9 to print success (+70, +280…) but **none persisted to DB**. Second run: every `/playlists/{id}/items` call returns **403**.
- Hypotheses: (a) silent commit/rollback bug (sqlite3 connection context manager); (b) Spotify dev-mode is 403'ing playlist reads now; (c) partial API ban from the earlier volume.
- Action on resume: investigate commit behavior; retry playlist pull later once 403s clear. Could also pull just "Let's Groove" by ID.

## Known constraints
- Spotify dev mode: 50 req / 30s rolling; 429 → multi-hour ban. Library endpoints seem laxer than search.
- Last.fm: 5 req/s.
- Python 3.9 (no `X | None` syntax).
- `_guard()` in pull_library.py handles 429 (bail), transient network errors (3x retry), 403 on playlists (skip).

## Files
- `auth.py` — `get_spotify()`, `.cache-library` token
- `db.py` — SQLite schema
- `pull_library.py` — library pull, --skip-liked/--skip-recent/--skip-playlists
- `library.db` — 15,012 tracks in `tracks`, sources in `track_sources`
- `.env` — has `LASTFM_API_KEY` (copied from putmeon)

## Next steps (can parallelize)
1. **Piece 5 (tag mapper)** — pure code, no APIs. Build `tag_map.yaml` + `classify.py`.
2. **Piece 4 (Last.fm)** — independent API, low ban risk. Build + run `lastfm_tags.py`.
3. **Piece 3 (audio features)** — Spotify API, **hold** until 403 situation clarifies.
4. **Playlist investigation** — why didn't commits persist?
   - Debugger ruled out sqlite3 context manager as the cause (verified with scratch test).
   - Concurrent-writer theory doesn't hold (lastfm_tags.py wasn't running yet).
   - Recommended defensive fix: enable WAL mode + busy_timeout=30000 in `db.connect()`. **Defer until lastfm_tags.py finishes** (can't change journal mode mid-run safely).
   - Root cause still unknown. Safe to retry once 403s clear.
