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
