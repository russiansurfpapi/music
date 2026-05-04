## Resume command: claude --resume
## Date: 2026-04-07
## Status: in-progress

---

# WHAT WAS DONE

## 1001Tracklists Scraper Fix
- Bright Data Web Unlocker `render: True` returning empty responses for 1001TL
- Root cause: 1001TL added Cloudflare Turnstile CAPTCHA that BD render can't solve
- **Fix**: Replaced `requests.post()` to BD API with **Playwright headless browser + Bright Data proxy**
  - Playwright navigates to page → gets CAPTCHA gate → auto-clicks submit button → page loads
  - Works for tracklist pages (simple form submit), NOT for DJ/search pages (Turnstile blocks)
- **Search fix**: Replaced 1001TL search/DJ page scraping with **Google search** (`site:1001tracklists.com/tracklist "artist name"`)
  - Paginates Google results for up to `--sets N` URLs
  - Prioritizes artist's own sets by URL slug matching
- Files modified: `tracklist_scraper.py` (fetch_html, search_and_get_sets, _search_google_for_tracklists)
- Added to `.env`: `BRIGHTDATA_CUSTOMER_ID=hl_02c0928d`, `BRIGHTDATA_ZONE_PASSWORD=98d5odj6xyfn`
- Added `playwright` to `requirements.txt`

## Spotify Integration Fixes
- Original app (`music-playlist-generator`, client ID `b7900d264a454d9696d6ef5669646bca`) rate limited repeatedly
- Created new app `music-scraper-2` (client ID `7186b73b1b4a48949e0990e8ed499f0e`)
- User Management: `alexander_podolsky@alumni.brown.edu` added to both apps
- `.env` updated with new credentials

### Rate Limit Fixes (multiple iterations):
1. **Disabled spotipy retry adapter**: Pass plain `requests.Session()` to avoid spotipy hammering 429'd endpoints
2. **SpotifyRateLimited exception**: Instant bail on 429, zero retries
3. **Local cache** (`spotify_cache.json`): `artist||title` → Spotify track ID. Never search same track twice.
4. **Batch processing**: Search 50 tracks → add to playlist → pause 60s → repeat
5. **Playlist created FIRST**: Tracks added incrementally, so partial progress is saved
6. **TRACK_DELAY = 4s**: ~8 calls per 30s window, well under dev mode limit
7. **Playlist creation**: Uses `/me/playlists` endpoint directly (not `sp.user_playlist_create` which 403s in dev mode)

## Spotify Sampler (main.py)
- Fixed scope: added `playlist-read-private playlist-read-collaborative` to OAuth
- Tested with artist "Sampler" — user authenticated interactively

## Concert Matcher (concert_matcher.py)
- New untracked file, read but not modified
- Matches Spotify listening history against Ticketmaster NYC concerts

## Files Created
- `spotify_cache.json` — 489 cached Spotify track lookups
- `shadow_child_tracks.csv` — 477 tracks
- `dj_koze_tracks.csv` — 172 tracks
- `sets/` directory — 63 saved HTML files from 1001TL
- `.cache` — Spotify OAuth token for music-scraper-2

## Files Modified
- `tracklist_scraper.py` — Major rewrite of fetch (Playwright), search (Google), Spotify (cache+batch)
- `.env` — New Spotify credentials, added BD customer ID + zone password
- `.mcp.json` — Updated Bright Data MCP token
- `requirements.txt` — Added playwright
- `main.py` — Added playlist-read scopes

## Key Line Numbers (tracklist_scraper.py)
- `fetch_html()`: ~92-140 — Playwright + BD proxy + CAPTCHA auto-submit
- `_get_browser()`: ~55-75 — Shared Playwright browser instance
- `_search_google_for_tracklists()`: ~177-230 — Google site search with pagination
- `get_spotify()`: ~464-479 — Plain session, no retry adapter
- `find_spotify_track()`: ~493-513 — Cache-aware search, raises SpotifyRateLimited
- `_get_or_create_playlist()`: ~516-540 — Uses /me/playlists endpoint
- Main Spotify loop: ~735-800 — Batch search+add with 60s pauses
- Config: `BATCH_SIZE=50, BATCH_PAUSE=60, TRACK_DELAY=4`

---

# WHAT WAS LEARNED

## Bright Data
- Web Unlocker API `render: True` returns empty (200, len=0) for 1001TL — Cloudflare Turnstile
- BD proxy (`brd.superproxy.io:33335`) works but needs Playwright for JS rendering
- Proxy auth: `brd-customer-{CUSTOMER_ID}-zone-{ZONE}:{PASSWORD}@brd.superproxy.io:33335`
- Customer ID `hl_02c0928d`, zone `web_unlocker1`, password `98d5odj6xyfn`
- Need `ignore_https_errors=True` in Playwright context (BD proxy rewrites certs)
- DJ/search pages have Turnstile CAPTCHA that doesn't auto-solve — Google search is the workaround
- Tracklist pages have a simpler CAPTCHA (form submit with hidden `captcha=1` field)

## 1001Tracklists
- Track data is 100% JS-loaded — non-rendered HTML has zero track content
- CAPTCHA gate: form with `captcha=1` hidden input + `cf-turnstile-response` field
- Tracklist pages: simple form submit bypasses it
- DJ/search pages: Turnstile widget blocks headless browsers
- `.trackValue` CSS selector is the primary extraction method
- Only 5/694 tracks have embedded Spotify URIs (0.7%) — can't skip search

## Spotify Dev Mode
- Rate limit: ~180 req/min documented, but dev mode is MUCH stricter (~50/rolling window)
- First 429 triggers 24-HOUR ban (Retry-After: 85000+ seconds)
- Ban is per-ACCOUNT, not per-app — new app on same account gets same ban
- Free Spotify accounts can't use Web API (Premium required since Feb 2026)
- Extended Quota Mode requires 250k+ MAU — not available for personal apps
- `sp.user_playlist_create()` returns 403 in dev mode — use `/me/playlists` directly
- spotipy's default retry adapter (urllib3 Retry) hammers 429'd endpoints, escalating ban from minutes to 24h
- Fix: pass plain `requests.Session()` to bypass retry adapter entirely

## Wrong Hypotheses
- "Bright Data API key might be wrong" → Key was fine, render was the problem
- "New Spotify app will bypass rate limit" → Ban is per-account
- "1s delay between tracks is enough" → Got 429'd at track 489
- "2s delay is enough" → Got 429'd at track 495
- "retries=0 in spotipy disables retries" → spotipy's custom Retry class in util.py ignores it

---

# WHAT IS PENDING

## Immediate Next Step
- Rate limit clears (triggered ~2026-04-07, clears ~2026-04-08)
- Run: `python3 tracklist_scraper.py --local sets/ --playlist "Shadow Child Sets"`
- Cache has 489 entries → only ~158 uncached tracks need searching
- With 4s delay, should take ~12 min and stay well under limits

## After Shadow Child
- DJ Koze playlist: `python3 tracklist_scraper.py --local sets/ --playlist "DJ Koze Sets"`
  - Need to separate DJ Koze HTML files from Shadow Child's in `sets/`
  - Or just run with `--artist "DJ Koze"` for fresh scrape

## Unanswered
- What's the exact dev mode rate limit? (Spotify doesn't publish it)
- Is 4s delay enough or do we need 5s? (489 tracks worked at 2s before hitting limit)

## Nothing Running in Background
- All background tasks completed or killed

---

# CRITICAL CONTEXT

## Spotify Credentials (current)
- App: `music-scraper-2`
- Client ID: `7186b73b1b4a48949e0990e8ed499f0e`
- Client Secret: `1ae4fe07b2f5452694523b5bd61515f2`
- User Management: `alexander_podolsky@alumni.brown.edu`
- Old app (`b7900d264a454d9696d6ef5669646bca`) still exists but we switched away

## Playlist Status
- "Shadow Child Sets" exists: https://open.spotify.com/playlist/0QP4eHZrie7pgxhHO0ksW9
- Contains 489 tracks (of ~647 total, ~158 remaining)
- Empty "Shadow Child Sets" playlist may also exist (created during earlier failed attempt)

## Bright Data Credentials
- API Key: `2b576f4c-c779-4314-a82e-ae7ae110cd2a`
- Customer ID: `hl_02c0928d`
- Zone: `web_unlocker1`, Password: `98d5odj6xyfn`
- MCP token in `.mcp.json`: same as API key (updated this session)

## Key Design Decisions
- Playwright over BD API render: BD render returns empty, Playwright + proxy works
- Google search over 1001TL search: Turnstile blocks DJ/search pages
- Cache + batch + incremental: Only way to handle 600+ tracks in dev mode
- 4s delay: Conservative estimate to avoid 24h bans
- Plain requests.Session: Prevents spotipy from escalating rate limits
