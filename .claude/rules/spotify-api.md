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
