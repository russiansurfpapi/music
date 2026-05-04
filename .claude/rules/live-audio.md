# Live Audio Identification

Patterns for identifying tracks in real-time audio, both from YouTube sources (`identify_youtube_set.py`) and live capture (`live_listen.py`).

## Backends

### Shazam (shazamio — unofficial)
- `from shazamio import Shazam; await shazam.recognize(wav_path)`
- Free, no API key
- Works on 12-20 second mono 16kHz WAV chunks
- Unofficial endpoint — keep polls ≥0.8s apart, back off on errors
- Returns dict with `track.title` and `track.subtitle` (artist) when matched; `track=None` on no match
- Rate limit: undocumented. Observed stable at 1 call / 12s over hours.

### ACRCloud
- Host: `identify-us-west-2.acrcloud.com`
- Signature scheme: HMAC-SHA1 of `"POST\n/v1/identify\n{key}\naudio\n1\n{timestamp}"` (see `identify_youtube_set.acr_identify`)
- Free trial: 14 days, 3000 requests
- More reliable than Shazam under noisy conditions; better for live mic capture
- Response: `status.code=0` + `metadata.music[0]` when matched; score is 0-100 (normalize to 0-1)
- Credentials stored in `.env` (ACR_HOST, ACR_KEY, ACR_SECRET) — loaded via `from dotenv import load_dotenv; load_dotenv()`

## Audio capture on macOS

### ffmpeg + avfoundation
List devices:
```bash
ffmpeg -hide_banner -f avfoundation -list_devices true -i ""
```

Capture 12s of mono 16kHz to WAV:
```bash
ffmpeg -y -hide_banner -loglevel error -f avfoundation -i ":0" -t 12 -ac 1 -ar 16000 out.wav
```

Device notation: `":0"` = default audio input. Second/third colon-prefixed indices appear when loopbacks (BlackHole) are installed.

### BlackHole loopback (for clean system audio)
```bash
brew install blackhole-2ch
```
- Then System Settings → Sound → Output → BlackHole 2ch (silent) OR set up Multi-Output Device to hear + capture
- `ffmpeg --list-devices` will show BlackHole as an additional audio device index
- Required for capturing Spotify/YouTube/Logic output. Mic-in-the-room also works but is noisier.

## Chunk strategy

- **Chunk length**: 12-20s. Shorter than 10s misses songs; longer than 25s wastes time on redundant identifications.
- **Step**: for pre-recorded sets (`identify_youtube_set.py`), step every 30-45s → each chunk overlaps slightly with the next song boundary. For live (`live_listen.py`), step=chunk (no overlap needed — one chunk = one check).
- **Dedup consecutive matches**: one track spans multiple chunks. Collapse (artist_lower, title_lower) runs into a single entry. See `identify_youtube_set.dedupe()`.

## Integration with library.db

Identified tracks flow through:
1. `lookup_library(artist, title)` — fuzzy match against `tracks` table (accent-stripped normalization in Python, not SQL LIKE — SQL LIKE misses accents)
2. If hit: get existing `subgenre`/`genre`/`texture` from `classifications`. Append to `.sketch_state.json` with full enrichment.
3. If miss: append as unclassified. Track counts but doesn't influence fork calculations until the next classified track lands.

## ID-placeholder guard (applies to all fingerprint backends)

NEVER resolve `(artist='ID', title='ID')` or variants `('unknown','?','i.d.')` — a real Spotify artist is literally named "ID" and will be matched by fuzzy resolvers, poisoning every downstream classification. See `_is_unidentified()` in `resolve_dj_tracks.py` and the ID-skip in `dj_archetype.py` signature detection.

## Known issues

- **Shazam on noisy mic capture**: room audio with mid-volume speakers hits ~30-50% miss rate. BlackHole loopback bumps that to ~85%+.
- **ACR not yet wired into `live_listen.py`**: as of 2026-04-22 session, only `identify_youtube_set.py` supports both backends. Next wire-up: copy `acr_identify()` into `live_listen.py` and gate via `--backend acrcloud`.
- **Shazam false-positives on remixes**: frequently IDs the original track instead of the remix used. Acceptable for set-shape analysis; less so for precise tracklisting.
