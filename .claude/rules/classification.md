# Classification (6-layer tag-based)

Rules for `classify.py` + `tag_map.yaml`. Six layers: genre, subgenre, production_dna, rhythm, texture, lineage.

## Anti-patterns discovered 2026-04-22

### Meta-tag pollution
- Tags like `electronic`, `dance`, `edm`, `electronica`, `club`, `electronic dance music` are **not** genres — they're audience/platform descriptors.
- Previously these were under `genre.house` in `tag_map.yaml`. This inflated house to 40% of the library because Spotify's `artist_genres` heavily tags "electronic".
- **Fix applied**: removed these from `genre.house`. Keeping only `house`, `house music`. Genre is now derived from subgenre via `SUBGENRE_TO_GENRE` fallback when only meta-tags are present.
- Outcome: house dropped 6,029 → 3,911 tracks (-35%), with 508 correctly-NULL genres replacing what was false-positive "house".

### The ID-placeholder bug
- DJ-set tracklists use "ID - ID" as a placeholder for unidentified tracks.
- Spotify has an actual artist **literally named "ID"** who makes progressive trance. Fuzzy track-resolver matched all 204 "ID - ID" rows to his one track (`spotify_id=5nrbuDZe5Hod1nUsND6QUA`).
- Every affected DJ had false "progressive trance" classifications — Mochakk 44 tracks, Shadow Child 54.
- **Fix**: `_is_unidentified(artist, title)` guard in `resolve_dj_tracks.py` cache + search paths. Skip set: `{"id", "i.d.", "unknown", "?"}`. Also nullified all existing `dj_set_tracks.spotify_id` where raw_artist = "ID".
- **Apply this pattern to any future fuzzy-match resolver** (Shazam/ACRCloud/etc.). Placeholder → don't resolve.

### Ambiguous single-word tags
- `minimal`, `industrial`, `progressive` each map to multiple subgenres unambiguously on their own but collide in practice.
- Fix: `_disambiguate()` in `classify.py` adjusts scores based on co-tags:
  - `minimal` + house cues → suppress `minimal techno` score ×0.3, boost `deep house`
  - `industrial` + hip-hop/rock cues → suppress `industrial techno` ×0.2
  - `progressive` + trance cues → boost `progressive trance`, suppress `progressive house` ×0.4

### Subgenre → DNA inference
- Production DNA (909, 808, 303, breakbeat-based, etc.) is rarely tagged in Last.fm community tags.
- `SUBGENRE_DNA` in `classify.py` adds +15 score to canonical DNA-subgenre lineages after subgenre is determined. E.g., `chicago house → 909`, `acid house → 909 + 303`, `trap → 808`, `footwork → 808 + breakbeat-based`.
- Outcome: 909 DNA coverage went 0 → 1,018 tracks; 808 went 1,205 → 1,724.

## Classification coverage baseline (post-April 2026 overhaul)

- ~16,458 classifications for ~16,674 tracks
- 4,090 → 3,954 NULL-subgenre tracks (modest improvement; the tail is tracks with only weak Last.fm tags)
- 508 tracks have `genre=NULL` — these previously had "house" via meta-tags and are now correctly unclassified pending better signal

## Thresholds for archetype visibility

- `dj_profiles.gather(min_resolved=15)` — default. DJs below this don't appear in `--all` archetype output.
- `dj_archetype.py --min-tracks <N>` CLI flag added this session for thin profiles (e.g., Cyril Hahn's 9 classified tracks use `--min-tracks 8`).

## Last.fm bypass (no-Spotify path)

When Spotify is banned or unavailable:
1. Compute synthetic spotify_id: `"lfm:" + md5(f"{artist.lower()}||{title.lower()}")[:16]`
2. Insert into `tracks(spotify_id, artist, title, lastfm_status='pending')`
3. Set `dj_set_tracks.spotify_id` to the synthetic ID
4. Run `lastfm_tags.py` — it keys on `(artist, title)` and stores tags under the synthetic spotify_id
5. Run `classify.py` — it treats spotify_id as opaque; `lfm:*` prefix works fine

This lets analysis continue through any Spotify outage. Upgrade later via `resolve_dj_tracks.py search`.

## Known classifier weaknesses

- Soulful house folds to deep house (community tags rarely say "soulful house" explicitly)
- Some rock/pop remixes of house tracks get misclassified as "rock" (they inherit rock artist-genre tags)
- "Kidnap, Chambray — Sea Breeze" tagged as "rock" is a known false positive
