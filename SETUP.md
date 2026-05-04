# Setup — Plug in with your own Spotify API key

This repo is wired to a single Spotify account via `.env`. To run it against a different account, you need your own Spotify dev app + API key and (optionally) a Last.fm key. No code changes required — just new env vars.

## 1. Clone and install

```bash
git clone https://github.com/russiansurfpapi/music.git
cd music
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

## 2. Make your Spotify app

1. Go to https://developer.spotify.com/dashboard → **Create app**
2. Fill in anything for name/description
3. **Redirect URI:** `http://127.0.0.1:8888/callback` (exact, with the trailing `/callback`)
4. Save, then copy the **Client ID** and **Client Secret** from the app's Settings page
5. In **User Management** (also in Settings), add the Spotify email of whoever will run the scripts. Dev-mode apps only work for accounts that are explicitly added here.

## 3. (Optional) Last.fm key

Tagging in `lastfm_tags.py` needs a Last.fm API key. Get one free at https://www.last.fm/api/account/create. If you skip this, you can still pull your library and analyze DJ sets — you just can't run Step 2 (tagging).

## 4. Write `.env`

Create `.env` in the repo root:

```env
SPOTIPY_CLIENT_ID=your_client_id_here
SPOTIPY_CLIENT_SECRET=your_client_secret_here
SPOTIPY_REDIRECT_URI=http://127.0.0.1:8888/callback
LASTFM_API_KEY=your_lastfm_key_here
```

`.env` is gitignored — it won't leak back to GitHub.

Optional extras (only needed for the matching scripts, safe to omit):
- `TICKETMASTER_API_KEY` — for `concert_matcher.py`
- `OPENAI_API_KEY1` — for `festival_playlist.py` and `songkick_scraper.py`
- `BRIGHT_DATA_API_KEY` — for `tracklist_scraper.py` (1001TL)

## 5. First-run auth

The first Spotify call opens a browser for OAuth. After you approve, a token is cached locally:

- `.cache-library` — used by the library / sessions / DJ pipeline (`auth.py`)
- `.cache` — used by `main.py`, `tracklist_scraper.py`, `festival_playlist.py`
- `.cache-matcher` — used by `concert_matcher.py`

All three are gitignored. Nuke any of them to force re-auth.

Verify auth works:

```bash
python3 auth.py
# → Authed as: Your Name (your_user_id) — premium
```

## 6. Reset local state

The DB and caches belong to whoever populated them. Before your first real run, clear them so you don't mix libraries:

```bash
rm -f library.db library.db-*
rm -f spotify_cache.json
rm -f .cache .cache-library .cache-matcher
```

Then follow the pipeline in `README.md` starting at **Step 1: Pull your library**.

## Gotchas worth knowing before you run anything

Dev-mode Spotify is strict. The rules in `.claude/rules/spotify-api.md` are real — read them:

- **~50 requests per rolling 30s window.** The code already throttles, but if you parallelize scripts across terminals, you *will* get 429'd.
- **A 429 bans your Spotify account for 13 min → 24 h.** Bans are per-account, not per-app — making a new Client ID won't rescue you.
- **Never run back-to-back heavy scrapes.** Give it a 5-minute gap between artist / DJ runs.
- **Dev mode blocks some endpoints** (403). The code already works around the known ones; if something you write hits a new 403, don't retry — check the rules file.

If you hit a ban mid-run, the scripts are designed to bail cleanly and resume from cache next time — don't re-trigger them trying to finish.

## Where to look when something breaks

| Symptom | Check |
|---|---|
| OAuth opens but loops | Redirect URI in Spotify dashboard must match `SPOTIPY_REDIRECT_URI` exactly |
| 403 on playlist create | Your email is missing from the app's User Management |
| 403 on endpoints that should work | Dev-mode restriction — see `.claude/rules/spotify-api.md` |
| 429 immediately | You're still banned from a previous run; wait it out |
| Last.fm returns nothing | Key missing or typo in `.env`; verify with `curl` |
| Playwright errors in tracklist scraper | `playwright install chromium` |

## What you get after a full run

- `library.db` — ~15k rows tagged across 6 layers (genre / subgenre / production DNA / rhythm / texture / lineage)
- ~100+ auto-generated playlists on the target Spotify account, prefixed `🎧 Study · ` and `🎧 3% · `
- `DJ_PROFILES.md` — subgenre signatures for every DJ whose sets you ingested
- `spotify_cache.json` — persistent search cache so subsequent runs don't re-query
