# Genre DNA — Spotify Ear Training System

A set of tools that pull your Spotify library, tag every track with deep musical DNA (genre, subgenre, production style, rhythm, texture, lineage), and auto-generate playlists that *teach* you something instead of just vibing.

Built for passive listening (subway, cooking) by a former DJ who wanted to hear *why* music sounds the way it does.

## What it does

1. **Pulls your full Spotify library** into a local SQLite database (~15k tracks).
2. **Tags every track** using Last.fm community tags, mapped across 6 layers:
   - **Genre** (house, hip-hop, techno…)
   - **Subgenre** (deep house, boom bap, acid techno…)
   - **Production DNA** (808, 909, TR-303, sampled breaks…)
   - **Rhythm** (four-on-the-floor, dembow, half-time, breakbeat…)
   - **Texture** (warm, cold, filtered, raw…)
   - **Lineage** (Chicago, Detroit, Kingston, Düsseldorf…)
3. **Generates playlists** in several formats and pushes them to Spotify:
   - **3% Chains** — 5-6 track "lessons" where each song is a tiny shift from the last. The order *is* the lesson.
   - **Evolution** — chronological, 50-80 tracks per genre. Watch a genre grow up.
   - **A/B** — side-by-side comparison (deep house vs tech house).
   - **Thread** — one element (a drum break, a synth sound) traced across decades and genres.
   - **Focus** — deep immersion in one subgenre, 50-500 tracks.
4. **Analyzes DJ sets** — ingest 1001Tracklists HTML, resolve tracks, analyze subgenre flow, rhythm shifts, and production DNA across sets.

## Quick start

### Prerequisites

- Python 3.9+
- Spotify Developer app (dev mode is fine)
- Last.fm API key
- `.env` file with credentials:
  ```
  SPOTIPY_CLIENT_ID=...
  SPOTIPY_CLIENT_SECRET=...
  LASTFM_API_KEY=...
  ```

> **Running this on a different Spotify account?** See [SETUP.md](SETUP.md) for the full walkthrough — creating the Spotify dev app, adding your user, wiping local caches, and dev-mode gotchas.

### Install

```bash
pip install -r requirements.txt
```

### Step 1: Pull your library

```bash
python3 pull_library.py
```

This fetches all liked songs, recent listens, and playlist tracks into `library.db`. Takes ~5 min for a large library.

Options:
- `--skip-liked` / `--skip-recent` / `--skip-playlists` to pull selectively.

### Step 2: Tag with Last.fm

```bash
python3 lastfm_tags.py
```

Enriches every track with community tags (track-level → artist-level → fallback). Rate limited to 5 req/s. For 15k tracks, expect ~1 hour.

### Step 3: Classify into 6 layers

```bash
python3 classify.py
```

Maps raw Last.fm tags to canonical labels using `tag_map.yaml`. Pure local computation, no API calls. Unmapped tags logged to `unmapped_tags.log` for review.

### Step 4: See what you've got

```bash
python3 summarize.py
```

Prints distribution breakdown of your classified library.

### Step 5: Generate listening sessions

```bash
# Detect possible sessions from your library
python3 sessions.py detect

# Build all session playlists
python3 sessions.py build --all

# Push to Spotify
python3 sessions.py push --all
```

### Step 6 (optional): Build 3% Chains

The curated ear-training playlists defined in `virgil_chains.yaml`:

```bash
# Resolve track names to Spotify IDs
python3 virgil.py resolve

# Build chain playlists locally
python3 virgil.py build

# Push a specific chain to Spotify
python3 virgil.py push --id funky-drummer
```

See [LISTENING_GUIDE.md](LISTENING_GUIDE.md) for the recommended listening order and what to listen for in each chain.

## DJ set analysis

If you have saved 1001Tracklists HTML files in `sets/`:

```bash
# Ingest HTML into database
python3 ingest_sets.py

# Resolve tracks to Spotify
python3 resolve_dj_tracks.py cache    # check local cache first
python3 resolve_dj_tracks.py search   # then search Spotify API

# Fetch metadata
python3 dj_tracks_upsert.py

# Analyze a DJ
python3 dj_report.py --dj bonobo --evolution --repeats

# Cross-DJ comparison
python3 dj_report.py --compare "bonobo,four-tet"

# Analyze a specific set
python3 analyze_set.py --list          # see all sets
python3 analyze_set.py --dj bonobo     # pick a set

# DJ profiles (subgenre signatures, distinctiveness)
python3 dj_profiles.py --md DJ_PROFILES.md
```

## 1001Tracklists scraper

Separately, `tracklist_scraper.py` scrapes tracklists from 1001Tracklists and creates Spotify playlists from them:

```bash
# Scrape a specific tracklist URL
python3 tracklist_scraper.py --dj-url "https://www.1001tracklists.com/tracklist/..."

# Search for an artist's sets and scrape them
python3 tracklist_scraper.py --artist "Bonobo" --sets 3

# Use a locally saved HTML file
python3 tracklist_scraper.py --dj-local sets/bonobo_set.html --playlist "Bonobo Live"
```

## Other tools

| Script | What it does |
|--------|-------------|
| `main.py` | Artist sampler — top tracks + recent releases → Spotify playlist |
| `concert_matcher.py` | Match your Spotify top artists against Ticketmaster NYC events |
| `festival_playlist.py` | Paste a festival lineup → Spotify playlist (uses OpenAI to parse) |
| `suggest.py` | Daily listening suggestion based on trending gaps in your library |
| `aggregate_italo.py` | Score & aggregate italo disco across followed playlists |
| `audio_features.py` | Fetch Spotify audio features (tempo, energy, danceability) |

## Key files

| File | Purpose |
|------|---------|
| `library.db` | SQLite database (gitignored) |
| `tag_map.yaml` | Raw Last.fm tag → canonical 6-layer mapping |
| `virgil_chains.yaml` | Curated 3% Chain definitions |
| `spotify_cache.json` | Persistent track search cache (artist\|\|title → Spotify ID) |
| `LISTENING_GUIDE.md` | How to actually use the generated playlists |
| `STATUS.md` | Current build progress |
| `DJ_PROFILES.md` | Generated DJ analysis output |

## Spotify API notes

This runs in Spotify **dev mode** (no extended quota). Key constraints:
- ~50 requests per rolling 30s window
- First 429 triggers a multi-hour ban (per-account, not per-app)
- The code uses 4s delays between searches, 120s pauses between batches, and bails immediately on 429
- See `.claude/rules/spotify-api.md` for the full set of hard-won lessons
