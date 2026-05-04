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

### identify_youtube_set.py — YouTube DJ set → fingerprinted tracklist
- Pipeline: yt-dlp (wav mono 16kHz) → ffmpeg chunk → shazamio or ACRCloud → dedupe → `dj_set_tracks`
- Two backends: `--backend shazam` (free, default) or `--backend acrcloud` (needs ACR_* env vars)
- Extracts ISRC from Shazam responses, Spotify ID from ACRCloud responses
- Writes set_id = YouTube video ID; dj_slug auto-creates in `djs` table
- CLI: `<url> --dj <slug> [--backend shazam|acrcloud|merge] [--chunk 20] [--step 45] [--title ...] [--date YYYY-MM-DD]`
- Default chunk=20s, step=45s → ~1.3 queries/min of source audio. 88min set = 119 chunks, ~4min runtime.

### youtube_to_study.py — batch YouTube → full pipeline (added 2026-04-28)
- `--auto-dj`: auto-detects DJ slug from video title (handles @, |, —, show-name patterns)
- Dedupes URLs by video ID + skips already-fingerprinted sets in DB
- Pipeline: fingerprint → cache resolve → ISRC resolve → Spotify search → Last.fm → classify
- CLI: `python3 youtube_to_study.py URL1 URL2 ... --auto-dj [--dry-run] [--push]`

### resolve_dj_tracks.py — Spotify ID resolution (3 modes)
- `cache` — match against `spotify_cache.json` (free, no API calls)
- `isrc` — resolve via `isrc:` Spotify search (93% hit rate, near-zero false positives)
- `search` — artist+title Spotify search fallback (4s delay, instant bail on 429)

### build_set_playlist.py — fingerprinted set → Spotify playlist
- Uses pre-resolved `spotify_id` from DB directly (no API call needed)
- Falls back to `find_spotify_track()` for unresolved tracks
- Filters invalid IDs (`lfm:*`, `fp:*`, non-22-char)
- CLI: `--set <set_id>` or `--all-fingerprinted` or `--dry-run`

### 1003scraper.py, fetch_html.py, track_extractor.py — Legacy/utility scripts
- Older versions of the scraping pipeline, mostly superseded by tracklist_scraper.py
- track_extractor.py still useful for batch processing saved HTML: `python3 track_extractor.py --input sets/`

## Key Data Files
- `spotify_cache.json` — Persistent Spotify track ID cache (artist||title → ID)
- `sets/` — Saved 1001TL HTML files (63 files as of Apr 2026)
- `shadow_child_tracks.csv`, `dj_koze_tracks.csv` — Extracted track lists
- `.cache` — Spotify OAuth token (music-scraper-2 app)
- `.cache-matcher` — Separate Spotify token for concert_matcher
- `library.db` — SQLite hub. Tables: `djs`, `dj_sets`, `dj_set_tracks`, `tracks`, `track_tags`, `classifications`, `artist_genres`, `track_sources`, `aggregate_candidates`, `sessions`.
  - `djs` column is `name` (NOT `display_name`)
  - `dj_sets.source_file` is NOT NULL — YouTube-origin sets store the URL there (and in `youtube_url`)
  - `dj_set_tracks` has `timestamp_sec`, `confidence`, `source`, `isrc` cols for fingerprint-origin rows
  - Synthetic ID prefixes: `lfm:` (Last.fm bypass), `fp:` (fingerprint bypass) — filter these before Spotify API calls

## Config
- `.env` — Spotify creds, Bright Data creds, **ACRCloud creds** (ACR_HOST, ACR_KEY, ACR_SECRET)
- `.mcp.json` — Bright Data MCP server config
- `requirements.txt` — Dependencies including playwright, shazamio, flask (~2.0), werkzeug<2.4

---

## Analysis toolkit (added 2026-04-22)

### classify.py — 6-layer tag-based classifier
- `classify_track(raw_tags, artist_genres, release_year) -> dict`
- Reverse-index from `tag_map.yaml`. `_disambiguate()` for ambiguous single-word tags, `SUBGENRE_TO_GENRE` fallback, `SUBGENRE_DNA` inference (+15 to production_dna for canonical lineages).
- See `.claude/rules/classification.md` for anti-patterns.

### dj_archetype.py — 5-axis DJ fingerprint + qualitative layer
- Axes: breadth (entropy), flow (median run), anchor loyalty, own-tracks, era posture, risk
- Qualitative: interview quotes from `dj_interviews_raw.md`, vocab from `dj_interviews_vocab.yaml`, "vibe tag" cloud from raw Last.fm tags
- Also contains `_fp_axes()` for fingerprint-based axes (pace, dwell_cv, edit_density) using `dj_set_chunks` table
- CLI: `--dj`, `--compare`, `--all`, `--min-sets`, `--min-tracks`, `--md`

### set_shapes.py — timeline strip visualization
- One char per track (legend at bottom of output). Groups: `d`=deep house, `t`=tech house, `m`=minimal techno, `e`=electro, `T`=trance, `#`=hip-hop, `$`=disco-funk-soul, etc.
- Shape codes: PLATEAU / BLOCKS / WAVE / ECLECTIC / MIXED
- Transition rate, family-crossings, longest plateau, quintile tops

### set_energy.py — BPM + energy sparkline
- BPM from `SUBGENRE_BPM` midpoints (track-level BPM blocked by Spotify 403)
- Energy from texture layer (atmospheric → aggressive)
- Quintile averages for both

### transition_atlas.py — cross-DJ move catalog
- `SUBGENRE_BPM` dict with per-subgenre BPM ranges (canonical source)
- `FAMILY` dict groups (house, techno, trance, garage, dnb, dubstep, hiphop, disco-funk, electro-dance, global-club, pop-rock, ambient-dub, bass-uk)
- `classify_move(prev, next)` → PARALLEL / TEMPO_UP / TEMPO_DOWN / FAMILY_PIVOT / CURTAIN_DROP / SAME
- CLI: `--from <sg>` shows observed moves; `--map` writes full TRANSITION_ATLAS.md

### next_move.py — position-aware next-move advisor
- `--from <sg> --position N --hours H` ranked destinations with citations
- `--per-dj <dj1,dj2,dj3>` side-by-side DJ panels with 2-3 directions each + set citations

### set_planner.py — arc planner
- Given start + hours + optional `--like <dj>` / `--venue <v>`, outputs target track count, quintile composition, suggested transitions at boundaries, curveball placement

### set_sketch.py — iterative fork-based set builder
- State: `.sketch_state.json` (shared with web-app + live listener)
- Flags: `--seed`, `--pick`, `--track`, `--reset`, `--show-playlist`, `--like <dj1,dj2>`
- Advisories: plateau (2/3/4+ consecutive), family-run, subgenre dominance, quintile boundary
- `analyze_state(state) → list[dict]` returns signals with levels: info / nudge / warn

### space_miami_analysis.py — venue anatomy
- Filter: `set_id LIKE '%club-space-miami%' OR '%miami-music-week%'`
- Quintile arcs, cross-set "canon" (tracks ≥2 DJs play), per-DJ curveballs

### import_downloads.py — one-shot HTML → full pipeline
- Scans `~/Downloads/*.html`, verifies real tracklists (not shells), normalizes filenames
- DB-aware dedup via (dj-first-token, set-date) — catches manual-rename variations
- Runs: ingest → cache-resolve → Last.fm bypass (synthetic `lfm:<hash>` IDs) → lastfm_tags → classify → dj_archetype
- Zero Spotify calls — works through 429 bans

### live_listen.py — live Shazam → sketch state
- ffmpeg avfoundation capture → 12s chunks → shazamio → library lookup → append to `.sketch_state.json`
- Flags: `--device :0` (mic) or BlackHole index, `--hours`, `--chunk`, `--list-devices`, `--reset`
- Web-app auto-refreshes in live mode (detected via `identified_at` timestamps)
- Needs BlackHole for clean system audio (`brew install blackhole-2ch`)

### webapp/app.py + webapp/templates/index.html — Flask web UI
- http://127.0.0.1:5000 — Flask 2.0 + Werkzeug 2.3.8
- Shared `.sketch_state.json`, fork buttons, plateau advisories, DJ filter via `?like=`, 10s auto-refresh in live mode
- Routes: `/`, `/seed`, `/add`, `/undo`, `/reset`, `/api/state`
- See `.claude/rules/webapp.md` for schema + route details

---

## Generated analysis docs (rebuilt by scripts)

- `DJ_ARCHETYPES.md` — all DJ fingerprints (rebuilt by `dj_archetype.py --all`)
- `SPACE_MIAMI.md` — venue anatomy (rebuilt by `space_miami_analysis.py`)
- `SPACE_MIAMI_SHAPES.md` — timeline strips for all Miami sets (`set_shapes.py --venue space-miami`)
- `SPACE_MIAMI_ENERGY.md` — BPM/energy sparklines (`set_energy.py --venue space-miami`)
- `TRANSITION_ATLAS.md` — full cross-DJ move catalog (`transition_atlas.py --map`)
- `LISTENING_GUIDE.md` — curated listening curriculum (pre-session)
- `SETUP.md` — onboarding doc for collaborators (added 2026-04-22)

## Subagents (`.claude/agents/`)
- `dj-demo.md` — live demo guide for a guest exploring the toolkit
- `dj-scraper-planner.md` — audits DB coverage + plans scraping strategy + falls back to manual instructions

## Backups
- `library.db.bak` — pre-classification-overhaul (2026-04-20)
- `library.db.bak-2026-04-22` — post-session snapshot
