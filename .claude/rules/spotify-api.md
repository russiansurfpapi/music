# Spotify API Rules

## Rate Limits (Dev Mode)
- Dev mode limit is ~50 requests per rolling 30s window (NOT 180 as documented)
- First 429 triggers a 24-HOUR ban (Retry-After: 85000+ seconds)
- Ban is per-ACCOUNT, not per-app — creating new apps doesn't help
- Extended Quota Mode requires 250k+ MAU — not available for personal use

## Spotipy Configuration
- ALWAYS pass a plain `requests.Session()` to prevent spotipy's retry adapter from hammering 429s
- NEVER rely on `retries=0` — spotipy's custom Retry class in `util.py` ignores it
- spotipy's default behavior escalates 429 bans from minutes to 24 hours

## Playlist Creation
- `sp.user_playlist_create()` returns 403 in dev mode
- Use `/me/playlists` endpoint directly via `requests.post()`
- User must be added to app's User Management in Developer Dashboard

## Search Strategy
- ALWAYS use a local cache (`spotify_cache.json`) — never search the same track twice
- ALWAYS use batch processing: search N tracks → add to playlist → pause → repeat
- ALWAYS create playlist FIRST, add tracks incrementally
- Minimum 4s delay between search calls
- 60s pause between batches of 50
- On 429: save cache, add batch to playlist, bail immediately — ZERO retries

## Current Credentials
- App: music-scraper-2 (Client ID: 7186b73b1b4a48949e0990e8ed499f0e)
- User: alexander_podolsky@alumni.brown.edu in User Management
