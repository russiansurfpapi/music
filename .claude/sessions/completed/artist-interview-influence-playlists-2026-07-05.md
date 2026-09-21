# Artist Interview Research + Influence Playlists

## Resume command: claude --resume (check ~/.claude for session ID) --dangerously-skip-permissions
## Date: 2026-07-05
## Status: in-progress

---

## WHAT WAS DONE

### Interview Research (3 artists)
- **Don Toliver**: 14 sources, 15 vocab terms → `don_toliver_interviews_raw.md` (413 lines)
- **Kali Uchis**: 15 sources, 18 vocab terms → `kali_uchis_interviews_raw.md` (483 lines)
- **Rosalía**: 13 sources, 16 vocab terms → `rosalia_interviews_raw.md`
- All files follow same format: raw quotes by source → YAML vocab block at bottom
- YAML includes `subgenre_usage`, `influence_map`, `named_influences`, `recurring_concepts`

### Spotify Influence Playlists (3 created)
- **Don Toliver's Influences**: 49 tracks → `https://open.spotify.com/playlist/1wdeTUm1Tjue4Mqm0c8yJ4`
- **Kali Uchis's Influences**: 58 tracks → `https://open.spotify.com/playlist/65O6SLr3qgClPdwwmZ1bTD`
- **Rosalía's Influences**: 105 tracks → `https://open.spotify.com/playlist/2wsW2aqoWWYFd4f92fcY42`
  - Note: Rosalía v1 used simple search, not sampler. v2 sampler run was interrupted.

### Generic Pipeline Script
- Created `artist_influences.py` — CLI tool that reads interview YAML and builds Spotify playlists
- Upgraded to **sampler approach** (top tracks + recent releases + album deep cuts, from main.py pattern)
- Cross-artist dedup built in
- CLI: `--list-influences`, `--dry-run`, `--tracks-per N`, `--playlist-name`
- Default tracks-per raised from 3 to 5

### Files Created
- `don_toliver_interviews_raw.md` — interview extracts + YAML vocab
- `kali_uchis_interviews_raw.md` — interview extracts + YAML vocab
- `rosalia_interviews_raw.md` — interview extracts + YAML vocab
- `artist_influences.py` — generic CLI (sampler + dedup)

### Files Read (key lines)
- `dj_interviews_raw.md` — format reference (lines 1-100)
- `dj_interviews_vocab.yaml` — YAML format reference (lines 1-100)
- `main.py` — sampler pattern: `get_top_tracks` (341), `get_recent_releases` (354), `get_top_albums_tracks` (386)
- `tracklist_scraper.py` — playlist creation pattern: `_get_or_create_playlist` (650-677)

---

## WHAT WAS LEARNED

### Spotify Dev Mode Gotchas (confirmed this session)
- `/v1/users/{id}/playlists` POST returns **403** — must use `/v1/me/playlists` instead
- `sp.playlist()` in dev mode returns `items` at top level, NOT nested under `tracks`
- `sp.playlist_tracks()` works normally and is the reliable way to verify track counts
- `sp.search(q='artist:X', type='track')` is the working substitute for `artist_top_tracks` (403)

### Research Pipeline
- WebSearch + WebFetch via fork agents is effective for interview extraction
- Each artist takes ~6-8 minutes of research agent time
- Typical yield: 13-15 sources, 15-18 vocab terms per artist
- YouTube interviews are hard to extract — most results are summaries/fan content, not transcripts
- Bright Data MCP, SERP engine, IG pipeline (agent3a/social_media_research) exist but were NOT used this session

### YAML Parsing
- Interview files have YAML between ` ```yaml ` and ` ``` ` after `## Vocabulary Synthesis (YAML)`
- `influence_map` format varies: Don Toliver uses `"Artist — description"`, Kali Uchis uses `named_influences` flat lists
- Both formats handled by `extract_artist_from_influence_string()` and `extract_artist_from_named()`
- SKIP_TERMS filter needed for genre names that appear in influence lists (e.g., "Boleros (genre)")

### Mismatches Found
- "Lorna" (reggaeton artist, "Papi Chulo") matched **Lorna Shore** (deathcore band) in Rosalía's playlist
- "Selena" matched Selena Gomez initially — fixed by searching "Selena Quintanilla"
- "Pedro Almodóvar" and "Mozart" matched semi-relevant classical tracks — harmless but noisy

---

## WHAT IS PENDING

### Immediate Next Steps
1. **Rebuild all 3 playlists with sampler**: `artist_influences.py` was upgraded but only dry-run tested. The Rosalía v2 run was interrupted. Don Toliver and Kali Uchis playlists still use v1 (search-only, not sampler).
   - ⚠ **Another Claude session may be running Spotify calls** — check rate limit before running
   - Command: `python3 artist_influences.py "Don Toliver" "Kali Uchis" "Rosalía" --tracks-per 5`
   - Consider deleting v1 playlists first to avoid duplicates

2. **Better research pipeline**: User wants to wire in:
   - Bright Data MCP (`mcp__Bright_Data__scrape_as_markdown`) for full article scraping
   - SERP engine (`mcp__Bright_Data__search_engine`) for finding long-form interviews
   - YouTube transcript extraction (not built yet for interviews)
   - IG media pipeline (`agent3a/social_media_research/ig_media_pipeline.py`) for social content
   - The `ad_language_pipeline.py` pattern (SERP → score → Apify deep-read → LLM extract)

3. **"Lorna" fix**: Replace Lorna Shore with the reggaeton Lorna in Rosalía's playlist

### Unanswered Questions
- Should the interview YAML vocab be merged into the main `dj_interviews_vocab.yaml`?
- Does user want the festival-neighbors feature (artist → find festivals → sample lineup)?
- How many tracks per influence artist is ideal? (currently default 5)

---

## CRITICAL CONTEXT

### Playlist IDs
- Don Toliver: `1wdeTUm1Tjue4Mqm0c8yJ4`
- Kali Uchis: `65O6SLr3qgClPdwwmZ1bTD`
- Rosalía: `2wsW2aqoWWYFd4f92fcY42`

### Key Decisions
- Used fork agents for parallel research (one per artist) — effective, ~7 min each
- Chose `named_influences` + `influence_map` as YAML extraction targets (covers both interview file formats)
- Raised default tracks-per from 3 to 5 for sampler approach
- `/me/playlists` endpoint (not `/users/{id}/playlists`) for dev mode compatibility

### Rate Limit Awareness
- ⚠ Another Claude session may be consuming Spotify quota simultaneously
- Sampler approach uses ~3-4x more API calls per artist (artist search + album listing + album tracks)
- 36 influence artists × ~8 calls each = ~288 calls for one full sampler run
- At 4s delay = ~19 minutes per artist playlist build
- If 429 hits: script bails immediately, partial playlist is saved
