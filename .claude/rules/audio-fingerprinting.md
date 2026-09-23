# Audio Fingerprinting Rules

## Backends

### shazamio (unofficial Shazam, free)
- Primary backend — no API key, no cost, ~70-80% recall on mainstream DJ sets.
- API: `await Shazam().recognize(str(path))` returns `{"track": {"title": ..., "subtitle": ...}}` or `{}`.
- **artist is `subtitle`, track name is `title`** — counterintuitive, easy to reverse.
- Async — wrap in `asyncio.run()`.
- Confidence not exposed; store `1.0`.
- ~0.8s sleep between calls is safe pace. Tested 119 chunks with zero rate-limit errors.
- **Install side-effect**: `pip install shazamio` forces `pydantic 2.x → 1.10` and `numpy 2.x → 1.26`. Verify downstream scripts (classify.py etc.) still work after install.
- Unofficial — Apple can break it anytime. Has happened twice historically.

### ACRCloud (commercial)
- Purpose-built for mixes. Trial: 14 days / 3000 requests. ~4% of quota per 90-min set.
- Auth: HMAC-SHA1 signed POST to `https://{host}/v1/identify`.
- Credentials in `.env` as `ACR_HOST`, `ACR_KEY`, `ACR_SECRET`.
- `pyacrcloud` SDK exists but raw HTTP is simpler — script uses raw POST.

### What NOT to bother with
- Official Shazam API — Apple killed public dev program in 2018. ShazamKit is iOS/macOS-only.
- DIY audfprint — only worth it for IDing unreleased/bootleg tracks; needs reference audio at scale.
- Extracting `spotify:track:ID` from Shazam — impossible. Shazam only returns `spotify:search:` deeplinks.

### ISRC extraction (added 2026-04-28)
- Shazam responses include `track.isrc` — a unique per-recording identifier
- ACRCloud responses include `track.external_metadata.spotify.track.id` — direct Spotify ID
- ISRC → Spotify: `sp.search(q='isrc:XXXX')` gives ~93% hit rate, zero false positives
- `identify_youtube_set.py` now stores ISRC in `dj_set_tracks.isrc` column
- `resolve_dj_tracks.py isrc` resolves via ISRC before falling back to artist+title search

## Pipeline shape (`identify_youtube_set.py`)
1. `yt-dlp -x --audio-format wav --postprocessor-args "-ac 1 -ar 16000"` — mono 16kHz WAV straight out.
2. `ffprobe` for duration, then `ffmpeg -ss T -t CHUNK` to slice.
3. Chunk=20s, step=45s is a good default. Tracks play >60s in DJ sets, so 45s step catches each one at least once.
4. Dedupe consecutive chunks with identical `(artist, title)`.
5. Write to `dj_set_tracks` with `timestamp_sec`, `confidence`, `source`, `isrc` cols.

## Batch ingestion (`youtube_to_study.py --auto-dj`)
- Paste multiple YouTube URLs, auto-detects DJ name from video title
- Dedupes by video ID + skips already-fingerprinted set_ids in DB
- Full pipeline: fingerprint → cache resolve → ISRC resolve → Spotify search → Last.fm → classify
- `--dry-run` to preview DJ detection without running
- `--push` to rebuild Study playlists at the end

## Recall patterns on electronic DJ sets
- Mainstream / released tracks: strong recall (3+ consecutive chunk hits).
- Unreleased / ID tracks / heavy edits: dead zones of 3-5 min with zero matches. No backend fixes this.
- False-positive signature: single outlier track sandwiched between consistent matches — usually a similar-sounding synth lead fooled the fingerprinter.
- Pitch-shifted tracks (±6% BPM typical for DJs) are where vanilla fingerprinters lose recall. shazamio is surprisingly robust; haven't measured ACRCloud yet.

## Dedupe gotcha
String-exact dedupe on `(artist, title)` misses remix variants of the same track (e.g., "X" vs "X (Remix)"). Acceptable for first pass; improve with substring/fuzzy match if noise becomes a problem.

## Finding the sets (`discover_dj_sets.py`, Sept 2026)

Supersedes `discover_sets.py`, which asked Claude a yes/no question per SerpAPI
result. Asked for 10 Nicolas Jaar sets it returned 4; the same catalogue
searched this way yielded 18 distinct events.

```bash
python3 discover_dj_sets.py "Nicolas Jaar" --dj nicolas-jaar
python3 discover_dj_sets.py "Four Tet" --kinds dj_set,live --min-minutes 40
python3 discover_dj_sets.py "Peggy Gou" --dj peggy-gou --run
```

- **Search is `yt-dlp ytsearch`, not SerpAPI** — free, and durations are real.
  SerpAPI's rich snippets left duration blank on most rows, so the length
  filter could not run at all.
- **21 query angles.** The venue/festival and recency angles matter more than
  they look: generic queries return the same famous uploads repeatedly, and it
  was the venue angle that surfaced Bar 25, 10 Days Off, Bozar and the
  artist's own radio project — four events no generic query found.
- **The LLM assigns an `event_key`, not a yes/no.** One event is uploaded many
  times: Jaar's 2012 Essential Mix appeared 9 times in one sweep, Sonar 2012
  five times. A yes/no filter says yes to all of them. Longest upload of each
  key wins, because ad-trimmed and partial re-uploads are common.
- **It also marks `owned`** — the same event under a different video ID. ID
  matching alone is not enough: the first run returned the Essential Mix,
  RA.500, RA.211 and Boiler Room NYC as new finds when all four were already
  in the library. The owned set titles go into the prompt.
- `--kinds` defaults to `dj_set` only. See below for why.

## Live performance is not a DJ set

Fingerprinting recognises *released recordings*, so an artist performing their
own material returns almost nothing. Measured on the same artist, same pipeline:

| set | yield |
|---|---|
| Resident Advisor RA.211 (radio mix) | 16 tracks, 71% |
| BBC Essential Mix (radio mix) | 27 tracks, 63% |
| Sonar 2012 (live performance) | **1 track, 3%** |
| Sodra Teatern (live performance) | **3 tracks, 6%** |

A 20x spread. Filter on this before spending an hour, and treat anything billed
"Live" as suspect even when the artist is a DJ.

## Throttles, and what each one looks like

- **Shazam** cuts off around ~1,500 queries in a session: every `recognize()`
  then times out. Recreating the aiohttp session does not help — it is
  IP-level. Spread large batches across days.
- **YouTube** answers "Sign in to confirm you're not a bot" after a dozen
  back-to-back downloads, and throttles bandwidth to ~100KiB/s before that.
  Sleep 45s between sets. It clears overnight.
- **yt-dlp picks a format that 403s.** Left alone it chooses 251 (opus, via the
  visionos player client) and YouTube refuses that media URL while format 140
  (m4a) downloads fine. `ydl_download` pins `140/bestaudio[ext=m4a]/...`.

## Results are only written at the end of a set

`identify_youtube_set.py` writes `dj_set_tracks` after the last chunk, so a
throttle or a kill loses the whole run. That has cost two partial runs of a
337-minute set (~170 chunks each time). **Incremental writes are the fix and
are not done yet.**
