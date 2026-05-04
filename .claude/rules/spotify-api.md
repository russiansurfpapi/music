# Spotify API Rules

## Rate Limits (Dev Mode)
- Dev mode limit is ~50 requests per rolling 30s window (NOT 180 as documented)
- There is also an undocumented longer-term sustained-rate quota — back-to-back runs totaling 300+ searches trigger 429 even with proper per-call delays
- First 429 triggers a ban with variable Retry-After (13 min to 24 hours observed)
- Ban is per-ACCOUNT, not per-app — creating new apps doesn't help
- Extended Quota Mode is organizations-only as of May 2025 — not available for individual developers
- NEVER run back-to-back artist scrapes without a 5-minute cooldown between them

## Spotipy Configuration
- ALWAYS pass a plain `requests.Session()` to prevent spotipy's retry adapter from hammering 429s
- NEVER rely on `retries=0` — spotipy's custom Retry class in `util.py` ignores it
- spotipy's default behavior escalates 429 bans from minutes to 24 hours

## Dev Mode Endpoint Restrictions (as of Apr 2026)
- `sp.user_playlist_create()` returns 403 — use `/me/playlists` via `requests.post()`
- `artist_top_tracks` returns 403 — use search with `artist:Name` filter instead
- `artist_albums` limit parameter capped at 10 (not 20/50) — paginate with offset
- `sp.albums()` batch endpoint returns 403 — fetch individually with `sp.album(id)`
- `include_groups` param on artist_albums no longer works — filter by `album_type` client-side
- User must be added to app's User Management in Developer Dashboard

## Search Strategy
- ALWAYS use a local cache (`spotify_cache.json`) — never search the same track twice
- ALWAYS use batch processing: search N tracks → add to playlist → pause → repeat
- ALWAYS create playlist FIRST, add tracks incrementally
- Minimum 4s delay between search calls
- 120s pause between batches of 50
- 5-minute cooldown between separate artist runs
- On 429: save cache, add batch to playlist, bail immediately — ZERO retries

## Current Credentials
- App: music-scraper-2 (Client ID: 7186b73b1b4a48949e0990e8ed499f0e)
- User: alexander_podolsky@alumni.brown.edu in User Management

## Sustained-rate bans observed (added 2026-04-22)
- **Retry-After 38,929s (~10.8h)** observed on combined workload: `resolve_dj_tracks.py search` (191 calls at 5s) + `dj_tracks_upsert.py` (30 individual sp.track calls at 1.5s) in sequence = triggered within ~20 min of starting the upsert phase.
- Signal: the ban hits on `sp.track(id)`, not on `sp.search`. Per-call delays don't help once the sustained quota is burning.
- Per-account ban. No workaround via new app/user.

## Last.fm-only bypass pattern (added 2026-04-22)
- When Spotify is unavailable OR you want to avoid quota burn, use synthetic IDs:
  - `spotify_id = "lfm:" + hashlib.md5(f"{artist.lower()}||{title.lower()}").hexdigest()[:16]`
- Insert into `tracks(spotify_id, artist, title, lastfm_status='pending')`
- `lastfm_tags.py` matches by `(artist, title)` not `spotify_id`, so enrichment works unchanged
- `classify.py` keys on `spotify_id` but treats the string as opaque — works fine with `lfm:*` prefix
- Update `dj_set_tracks.spotify_id` to the synthetic ID so joins work
- Later, when Spotify recovers, `resolve_dj_tracks.py search` can upgrade `lfm:*` IDs to real Spotify IDs (search by artist+title, UPDATE the ID in both `tracks` and `dj_set_tracks`)

## Dev-mode endpoint additions (observed 2026-04-22)
- `sp.track(tid)` works individually (1-by-1) — this is how `dj_tracks_upsert.py` fetches metadata. Don't try `sp.tracks([...])` batch — returns 403.
- `sp.artist(aid)` works individually for artist-genre lookup — but consumes quota fast. Prefer `--skip-artists` flag on dj_tracks_upsert.py unless artist genres are needed.

## Do not repeat (added 2026-04-22)
- DON'T run `resolve_dj_tracks.py search` immediately followed by `dj_tracks_upsert.py` — this combo triggers the sustained-rate ban.
- DON'T run `dj_tracks_upsert.py` without `--skip-artists` on a fresh DB with 6k+ orphan artist IDs — it will try to backfill all of them at 1.5s each = 2.5 hours = guaranteed 429.

## ISRC-based resolution (added 2026-04-28)
- `sp.search(q='isrc:XXXX', type='track', limit=1)` — near-100% hit rate for known ISRCs
- Shazam provides ISRCs in its response (`track.isrc`) — stored in `dj_set_tracks.isrc`
- `resolve_dj_tracks.py isrc` resolves all ISRC-bearing tracks before expensive artist+title search
- Preferred resolution order: cache → ISRC → search (minimizes API calls + maximizes accuracy)

## Playlist creation during 429 ban (added 2026-04-28)
- `_get_or_create_playlist()` hangs indefinitely during a ban — no timeout built in
- `build_set_playlist.py` uses pre-resolved `spotify_id` from DB (zero API calls for resolved tracks)
- Filters synthetic IDs: `lfm:*`, `fp:*`, and any ID not exactly 22 alphanumeric chars
- Leading-dash video IDs (e.g., `-WQrrGreVrY`) require `--set="-ID"` syntax in argparse
