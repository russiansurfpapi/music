## Resume command: claude --resume [session-id] --dangerously-skip-permissions
## Date: 2026-04-22
## Status: done

---

# WHAT WAS DONE

## Goal
Research and prototype audio-fingerprinting pipeline to auto-tag YouTube DJ sets
(tracks + timestamps) into `library.db`, using free + commercial backends.

## Pipeline built: `identify_youtube_set.py`
End-to-end: YouTube URL → yt-dlp wav → ffmpeg chunking → shazamio/ACRCloud → dedupe → `dj_set_tracks` rows.

### Files created
- `identify_youtube_set.py` — main pipeline script (~210 lines, two backends)
- `.claude/sessions/completed/` — new directory for archiving done sessions

### Files modified
- `.env` — appended `ACR_HOST`, `ACR_KEY`, `ACR_SECRET` (ACRCloud trial)
- `library.db` schema:
  - `dj_sets`: added `youtube_url TEXT`, `duration_sec INTEGER`
  - `dj_set_tracks`: added `timestamp_sec INTEGER`, `confidence REAL`, `source TEXT`

### Files read
- `.env` — confirmed `.gitignore` covers it before writing creds (.gitignore line 2: `.env`)
- `import_downloads.py` lines 1-50 — understood existing HTML→DB→classify pipeline
- `library.db` `.schema dj_sets` / `.schema dj_set_tracks` / `.schema djs`
  - **djs table has `name` column, NOT `display_name`** (caught bug at line ~155 of identify_youtube_set.py)

### Bugs found + fixed
- `identify_youtube_set.py` INSERT OR IGNORE INTO djs used `display_name` — actual column is `name`. Fixed.
- Initial `load_dotenv` placement attempted to use `Path` before import — moved call to after `HERE = Path(__file__).parent` definition.

## Test run (completed, exit 0)
**Set:** `DJ Seinfeld b2b Demi Riquísimo | Miami Music Week | RAW CUTS`
- URL: `https://www.youtube.com/watch?v=ePJzsMgWOy8`
- video_id: `ePJzsMgWOy8`
- duration: 5331s / 88 min
- backend: `shazam` (free)
- chunk=20s, step=45s → 119 chunks, ~4 min runtime

**Result:** 76% hit rate (90/119 chunks), 67 unique tracks after dedupe, $0 cost.

Written to `library.db`:
- `dj_sets` row with `set_id='ePJzsMgWOy8'`, `dj_slug='seinfeld-demi-raw'`
- 67 `dj_set_tracks` rows with timestamps + `source='shazam'` + `confidence=1.0`

---

# WHAT WAS LEARNED

## shazamio (unofficial Shazam, Python)
- pip installs `shazamio-0.6.0` + `shazamio-core-1.1.2`. **Installation downgrades `pydantic 2.9→1.10` and `numpy 2.0→1.26`** — may break `classify.py` or other scripts depending on pydantic v2.
- API: `await shazam.recognize(str(path))` returns `{"track": {"title": ..., "subtitle": ...}}` or `{}`.
- `subtitle` is the artist, `title` is the track name. Counterintuitive.
- No auth, no API key, free. ~0.8s per query safe pace (I used 0.8s sleep between chunks).
- Undocumented rate limits; ran 119 chunks in ~2min with zero rate-limit errors. Probably fine up to a few hundred/hour.
- Confidence score not exposed — treating all matches as `1.0`.

## ACRCloud
- Trial: 14 days, 3000 requests. One 90-min set ~= 120 chunks = 4% of trial quota.
- Credentials live in `.env` as `ACR_HOST`, `ACR_KEY`, `ACR_SECRET`.
- `pyacrcloud` installed but I used raw HTTP (hmac-sha1 signed POST) for clarity — no SDK dependency in script.
- Not yet tested on a real set; stubbed out and ready.

## YouTube DJ set fingerprinting recall patterns
On 88-min b2b house/tech set:
- **High recall** (3+ consecutive chunk hits) on tracks played >2 min: Dam Swindle, Kitty Hall, Confidence Man, Luke Alessi, Jimmy Batt.
- **Dead zones** of 3-5 min with zero matches — almost certainly unreleased/edits/ID tracks. Shazam cannot match what's not in its catalog.
- **False-positive signature**: "Darude — Sandstorm" sandwiched between two matches of a different track at 78:00. Shazam keys on prominent synth leads and will mis-match similar melodies.
- **Dedupe string-fragility**: "REAL MOVE TOUCH" vs "REAL MOVE TOUCH (Demi Requismo Remix)" get separate rows even though they're the same track. Current dedupe is exact-match on (artist, title).

## The four approaches, ranked
1. **shazamio (free)** — 70-80% recall on mainstream sets. Best starting point. Unofficial, could break.
2. **1001TL cross-reference** — near-100% recall when tracklist exists, 0% when it doesn't. Already built in `tracklist_scraper.py`.
3. **ACRCloud** — purpose-built for mixes, ~$0.003/req. Best commercial option. Untested here.
4. **DIY audfprint with own reference DB** — only worth it for unreleased/bootleg ID, requires reference audio at scale.

## Wrong hypotheses
- "Shazam has an official API we can use" — Apple killed the public dev program in 2018. ShazamKit is iOS/macOS-only.
- "We need to build a reference DB for DIY fingerprinting" — shazamio's reverse-engineered client bypasses this entirely.
- Initial concern about 120 chunks taking hours — actually 2-3 min at 0.8s pace.

## Gotchas that would be painful to re-derive
- `dj_sets.source_file` is `NOT NULL`; for YouTube-origin sets I store the YouTube URL there (double-stored also in the new `youtube_url` col, intentional — lets existing code that reads `source_file` keep working).
- yt-dlp downloads to `.webm` by default then transcodes — `-x --audio-format wav` plus `--postprocessor-args "-ac 1 -ar 16000"` gives mono 16kHz straight out.
- `ffprobe` for duration is cleaner than parsing `ffmpeg -i` stderr.
- `shazamio` is async — must wrap calls in `asyncio.run()`.

---

# WHAT IS PENDING

## Immediate next step on resume
User was offered 4 options after the test run, has not picked yet:
1. Resolve the 67 tracks to Spotify via `resolve_dj_tracks.py --set ePJzsMgWOy8` (soft-link via `spotify_cache.json`, zero API calls if cached)
2. Re-run SAME set with `--backend acrcloud` to compare coverage in the 3 dead zones (49:30-52:30, 55:30-59:15, 81:45-85:30). Burns ~120 ACR trial requests.
3. Improve dedupe — fuzzy match on (artist, title) to collapse remix variants.
4. Move on to next DJ.

## Background tasks
- Monitor task `btqjpfo58` timed out naturally after the job finished. Nothing running.
- Main job `b0ly38po4` completed exit 0.

## Unanswered
- Is the `pydantic` v1 downgrade going to break `classify.py` / `dj_archetype.py`? Not yet re-tested post-install.
- ACRCloud recall on the same 88-min set — unknown until backend 2 is tested.
- Does the `seinfeld-demi-raw` slug collide with anything useful? User may want `dj-seinfeld` as primary slug, with this being one of his sets.

---

# CRITICAL CONTEXT

## Key decisions
- **Used `seinfeld-demi-raw` as dj_slug**: This is a b2b set and neither `dj-seinfeld` nor `demi-riquisimo` existed in the djs table. Script auto-creates the slug on insert. Could be re-attributed later.
- **Stored YouTube URL in both `source_file` and new `youtube_url` col**: `source_file` is NOT NULL and existing tooling reads it; new col is the semantic home. Harmless redundancy.
- **Chunk=20s, step=45s**: Each chunk is 20s of audio (plenty for Shazam), next chunk starts 45s later (so 25s gap). Tracks generally play >60s, so this step size catches every track at least once. Tradeoff: 45s step → 120 queries for 88min; 30s step → 180 queries, marginal recall gain.
- **0.8s sleep between shazamio calls**: Found no rate limiting at this pace; could try lower but no reason to.

## ACRCloud credentials (in .env)
- Host: `identify-us-west-2.acrcloud.com`
- Key: `29befde9fa80a0c78413e5cac2a7bc20`
- Secret: `3aanO2D1R49AsMiaqCWdQN7IQtvJhgj1VpDOUeCv`
- Trial expires ~2026-05-06 (14 days from today)
- Project: "My Project" (ID 99764)
- 3000 req quota; 120 used per typical set

## How to test ACRCloud backend
```
python3 identify_youtube_set.py <url> --dj <slug> --backend acrcloud
```
Env vars auto-load from `.env` via `python-dotenv`.

## How to query what was saved
```
sqlite3 library.db "SELECT position, timestamp_sec, raw_artist, raw_title
  FROM dj_set_tracks WHERE set_id='ePJzsMgWOy8' ORDER BY position;"
```

## Shazamio artist/title quirk
Shazam's response uses `subtitle` for artist, `title` for track — wired up correctly
at identify_youtube_set.py line ~106.
