## Resume command: claude --resume [session-id] --dangerously-skip-permissions
## Date: 2026-05-01
## Status: done

---

# WHAT WAS DONE

## Batch YouTube set ingestion (13 sets, 3 DJs)
Fingerprinted, resolved to Spotify, and built playlists for:
- **Ferra Black** (6 sets): Superior Ingredients Brooklyn, The Room Medellín x2, NØID Bergamo, Concord Music Hall, DEEPND.FM
- **Ruze** (4 sets): Mixmag Lab London, PIV Ibiza, SOUTH London, b2b w/ Ranger Trucco + Josh Butler @ range NYC
- **A-Trak** (2 sets): Defected Malta (Café Del Mar), Jubilee+A-Trak Lot Radio
- **Jubilee + A-Trak** (1 set): TheLotRadio 08-16-2023

## `youtube_to_study.py` — upgraded to auto-DJ batch tool
- Added `--auto-dj` flag: fetches video titles via yt-dlp, extracts DJ name from common patterns ("DJ @ Venue", "DJ | Event", "SHOW - DJ (ep)"), slugifies
- Added URL dedup (by video ID)
- Added DB dedup (skips already-fingerprinted set_ids)
- Added `--dry-run` flag
- Full pipeline: fingerprint → cache resolve → ISRC resolve → Spotify search → Last.fm tags → classify
- Handles multi-DJ batches in one invocation

## `identify_youtube_set.py` — ISRC extraction
- Added `_extract_isrc(track)` — pulls ISRC from Shazam response `track.isrc`
- Added `_extract_spotify_id(track)` — extracts from ACRCloud `external_metadata.spotify.track.id`
- Added `isrc` column to `dj_set_tracks` table
- ISRCs stored at fingerprint time for near-100% Spotify resolution later
- Updated summary to show Spotify ID + ISRC stats

## `resolve_dj_tracks.py` — new `isrc` subcommand
- `python3 resolve_dj_tracks.py isrc` — resolves tracks by ISRC via `isrc:` Spotify search
- 93% hit rate observed (77/83 ISRCs resolved in one pass)
- Near-zero false positives (ISRC is a unique recording identifier)

## `build_set_playlist.py` — uses pre-resolved IDs
- Now reads `spotify_id` from `dj_set_tracks` directly (marked with `●` in output)
- Only calls Spotify API for tracks without pre-resolved IDs
- Filters invalid IDs: `lfm:*`, `fp:*`, and any non-22-char-alphanumeric strings
- Dramatically reduces API calls for sets that went through the resolve pipeline first

## Files modified
- `youtube_to_study.py` — major rewrite (auto-dj, pipeline steps, dedup)
- `identify_youtube_set.py` — ISRC extraction, spotify_id extraction attempt, DB schema update
- `resolve_dj_tracks.py` — added `cmd_isrc()` subcommand
- `build_set_playlist.py` — pre-resolved ID support, invalid ID filtering

## Files NOT modified (but used)
- `lastfm_tags.py` — ran for enrichment
- `classify.py` — ran for classification (17,159 tracks total)

---

# WHAT WAS LEARNED

## Shazam response structure (critical discovery)
- `track.hub.providers[type=SPOTIFY]` gives a **search deeplink** (`spotify:search:...`), NOT a direct track ID
- You CANNOT extract `spotify:track:ID` from Shazam — it only gives search queries
- `track.isrc` IS available and is the correct path to Spotify resolution
- ISRC → `sp.search(q='isrc:XXXX')` gives ~93% hit rate with zero false positives

## ACRCloud response structure
- `track.external_metadata.spotify.track.id` — direct Spotify track ID available
- This IS a real ID, unlike Shazam's search deeplink
- ACRCloud is the only backend that gives direct Spotify IDs at fingerprint time

## Spotify 429 ban timing
- Observed: 31 searches triggered ban with Retry-After: 7835s (~2h10m)
- Combined with earlier session's 191-call resolve run — sustained quota burned
- Playlist creation (`_get_or_create_playlist`) hangs indefinitely during a ban (no timeout)
- Solution: schedule post-ban work via CronCreate

## Video ID edge cases
- Leading dash (`-WQrrGreVrY`) breaks argparse `--set` flag — must use `--set="-WQrrGreVrY"`
- Same issue in bash `for` loops — fixed with quoting

## YouTube title DJ extraction patterns (ranked by reliability)
1. `"DJ @ Venue"` / `"DJ Live @ Venue"` — most common, most reliable
2. `"DJ @Venue"` (no space) — works for TheLotRadio-style titles
3. `"SHOW - DJ (episode)"` — all-caps left side = show name, right side = DJ
4. `"DJ party heaters DJ set | Venue"` — regex before pipe-split
5. `"Generic Mix | DJ Name | Venue"` — second pipe segment when first is generic
6. `"DJ — rest"` / `"DJ - rest"` — only if left side isn't ALL-CAPS (show name)

## `fp:` prefix IDs
- Synthetic IDs from import_downloads.py's fingerprint bypass path
- Format: `fp:` + 16-char hex hash
- Must be filtered alongside `lfm:` when building playlists

## Python 3.9 compatibility
- `str | None` syntax fails — must use `Optional[str]` from typing
- `list[str]` in function signatures fails — use string annotations `"list[str]"`

---

# WHAT IS PENDING

## Nothing blocked — all work complete
- All 13 sets fingerprinted, resolved, playlisted, tagged, classified
- `youtube_to_study.py --auto-dj` ready for future batch ingestion

## Future improvements (not started)
- Make `--backend merge` default in `youtube_to_study.py` (uses both Shazam + ACRCloud)
- Wire ACRCloud into `live_listen.py`
- Add `--playlist` flag to `youtube_to_study.py` to auto-build playlists at the end
- Timeout on `_get_or_create_playlist` to avoid hanging during 429 bans

---

# CRITICAL CONTEXT

## Playlist URLs created this session

| DJ | Set | Playlist |
|----|-----|----------|
| Ferra Black | Superior Ingredients, Brooklyn | https://open.spotify.com/playlist/0mGd1yzoc3dGX78sDZ6RIO |
| Ferra Black | The Room 2/22, Medellín | https://open.spotify.com/playlist/2phM4zaHIPwCqJPCGkpVdy |
| Ferra Black | NØID Bergamo, Italy | https://open.spotify.com/playlist/0XMe3RrcqkyP8zfEgLLivR |
| Ferra Black | Concord Music Hall w/ Cloonee | https://open.spotify.com/playlist/3CNJLKFwDe7a4rYNhWPRdz |
| Ferra Black | The Room 222, Medellín | https://open.spotify.com/playlist/4XV1hlarYsO1zNooG4rgWK |
| Ferra Black | DEEPND.FM (012) | https://open.spotify.com/playlist/2wH9BzZeNYtnmATGIwHauz |
| Ruze | Mixmag Lab London | https://open.spotify.com/playlist/76qYUjC4vNuSMN8Si6dbpM |
| Ruze | PIV Ibiza | https://open.spotify.com/playlist/6RiWagsCXa3Zk87QgGV3x3 |
| Ruze | SOUTH London | https://open.spotify.com/playlist/5hoMXlQVj8Gx5IrWrdvSqR |
| Ruze/Trucco/Butler | b2b @ range NYC | https://open.spotify.com/playlist/0MkW5rRdxcno5m15XXxR9q |
| A-Trak | Defected Malta | https://open.spotify.com/playlist/7eg8bCbJjfgriGl36xPqE0 |
| Jubilee + A-Trak | Lot Radio | https://open.spotify.com/playlist/7ezggSvn2e32M23xR3Zhjy |

## Key command for future batch ingestion
```bash
python3 youtube_to_study.py URL1 URL2 URL3 ... --auto-dj
```
Auto-detects DJs, dedupes, fingerprints, resolves, tags, classifies. Add `--push` to rebuild Study playlists.
