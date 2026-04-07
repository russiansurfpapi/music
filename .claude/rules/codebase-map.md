# Codebase Map — Music Playlist Generator

## Core Scripts

### tracklist_scraper.py — 1001Tracklists → Spotify pipeline
- **Fetch**: Playwright + Bright Data proxy with CAPTCHA auto-submit (~line 92-140)
- **Search**: Google site search to find tracklist URLs (~line 177-230)
- **Extract**: 7 fallback strategies for track extraction (~line 320-430)
- **Spotify**: Cache-aware batch search + incremental playlist building (~line 462-800)
- CLI: `--artist`, `--sets`, `--local`, `--playlist`, `--dj-url`, `--dj-local`, `--output`

### main.py — Spotify artist sampler
- Takes artist names (CSV or file), finds top tracks + recent releases + top albums
- Creates/updates Spotify playlist "Escuchar" (or custom name)
- Has its own `artist_chooser()` with name similarity scoring (~line 271-325)
- CLI: `python3 main.py 'Artist 1, Artist 2' [playlist_name]`

### concert_matcher.py — Spotify listening → Ticketmaster NYC concerts
- Matches Spotify top artists against Ticketmaster events
- Standalone — no web app dependencies
- Uses `.cache-matcher` for separate Spotify auth scope (user-library-read)
- Reads TM API key from putmeon/.env.local

### festival_playlist.py — Festival lineup → Spotify playlist
- Uses OpenAI to extract artist names from festival lineup text
- Concurrent Spotify search with ThreadPoolExecutor

### 1003scraper.py, fetch_html.py, track_extractor.py — Legacy/utility scripts
- Older versions of the scraping pipeline, mostly superseded by tracklist_scraper.py
- track_extractor.py still useful for batch processing saved HTML: `python3 track_extractor.py --input sets/`

## Key Data Files
- `spotify_cache.json` — Persistent Spotify track ID cache (artist||title → ID)
- `sets/` — Saved 1001TL HTML files (63 files as of Apr 2026)
- `shadow_child_tracks.csv`, `dj_koze_tracks.csv` — Extracted track lists
- `.cache` — Spotify OAuth token (music-scraper-2 app)
- `.cache-matcher` — Separate Spotify token for concert_matcher

## Config
- `.env` — Spotify creds, Bright Data creds
- `.mcp.json` — Bright Data MCP server config
- `requirements.txt` — Dependencies including playwright
