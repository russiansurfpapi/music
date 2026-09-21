# Discover Influences Pipeline — SERP + YouTube + LLM + Spotify

## Resume command: claude --resume (check ~/.claude for session ID) --dangerously-skip-permissions
## Date: 2026-07-05
## Status: done

---

## WHAT WAS DONE

### New Script: `discover_influences.py`
Built a fully automated influence discovery pipeline from scratch:
1. **SERP search** (5 query angles via SerpAPI) — web articles + YouTube interviews
2. **Article scraping** (urllib + Bright Data `scrape_as_markdown` fallback for 403s)
3. **YouTube transcript extraction** (Deepgram Nova-2 via `--deepgram` flag, fallback to yt-dlp VTT auto-subs)
4. **LLM extraction** (Claude Haiku 4.5) — extracts 15-30 influence artists + named tracks/albums
5. **Fuzzy-deduped Spotify sampler** — multi-query search per artist, dedup by ID + fuzzy (artist, title)

### Playlists Created
- **Jamie xx — Influences**: 130 unique tracks / 28 artists → https://open.spotify.com/playlist/1Ztr2VX2Jo3OaCctkWANcz
- **Rosalía — Influences**: 139 unique tracks / 27 artists → https://open.spotify.com/playlist/4WM5mNdBxMDzYVuZFguUJF

### Files Created
- `discover_influences.py` — the full pipeline script (new)
- `jamie_xx_influences.json` — saved research data
- `rosalía_influences.json` — saved research data

### Files Modified
- `.env` — added `DEEPGRAM_KEY` (from agent3a) and `SERPAPI_KEY`

### Key Line Numbers in `discover_influences.py`
- `_normalize()`, `_fuzzy_eq()`, `dedup_influences()`, `_track_key()`: lines ~50-100
- `search_influence_articles()`: 5 SERP queries, returns up to 15 articles
- `search_youtube_interviews()`: 3 SERP queries, returns up to 8 videos
- `_fetch_urllib()` + `_fetch_bright_data()`: dual-path article scraper
- `_transcript_deepgram()`: yt-dlp audio download → Deepgram Nova-2 API
- `extract_influences_with_llm()`: Claude Haiku, 20K char content window, bracket-balanced JSON parsing
- `build_influence_playlist()`: multi-search sampler + fuzzy track dedup

---

## WHAT WAS LEARNED

### Anthropic API Key
- Only `claude-haiku-4-5-20251001` works on this API key (sk-ant-api03-4UN7...)
- `claude-sonnet-4-20250514` and `claude-3-5-sonnet-20241022` both return 404
- Credits ran out mid-session — user topped up

### Deepgram
- Key in `agent3a/.env`: `DEEPGRAM_KEY=62d53d07424b0d1a659d75dd531128f3be8ac31f`
- Nova-2 model works well for interview transcription
- Endpoint: `POST https://api.deepgram.com/v1/listen?model=nova-2&smart_format=true&language=en`
- Auth: `Token {key}` header
- Input: raw MP3 bytes, `Content-Type: audio/mp3`
- yt-dlp audio download sometimes fails (429 from YouTube) — VTT fallback handles it

### Fuzzy Dedup Patterns
- `difflib.SequenceMatcher` at 0.82 threshold catches remasters/live versions without false positives
- Track title suffix stripping needed for both `(Remastered 2011)` and `- 2017 Remaster` patterns
- Artist dedup merges tracks lists from duplicate entries (e.g., "Four Tet" appearing twice)
- `unicodedata.normalize("NFD")` + category filter strips accents (Björk → Bjork, Rosalía → Rosalia)

### LLM JSON Parsing
- Haiku sometimes emits text after the JSON array — `re.search(r"\[.*\]", text, re.DOTALL)` is too greedy
- Fixed with bracket-depth walker: find `[`, walk forward counting `[`/`]` depth, stop at depth 0

### Bright Data MCP
- `mcp__Bright_Data__scrape_as_markdown` works well for articles (TeachRock, Firebird, Hola all succeeded)
- Reddit scraping returns mostly nav/chrome, not post content
- Facebook URLs return 400/timeout consistently

---

## WHAT IS PENDING
- Script is complete and working — no blockers
- Could add: `--artist` flag to run multiple artists in one command
- Could add: integration with existing `artist_influences.py` (YAML-based) for artists that have interview files
- Deepgram integration could be added to `live_listen.py` (currently Shazam-only)

---

## CRITICAL CONTEXT
- The older `artist_influences.py` reads from hand-curated `*_interviews_raw.md` YAML files
- The new `discover_influences.py` is fully automated — no manual research needed
- Both scripts can coexist; `discover_influences.py` is the "just run it" version
- Spotify dev mode: only Haiku works on API key, only `/me/playlists` for creation
