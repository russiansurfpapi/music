# Web-App (Flask set sketcher)

Flask 2.0 app at `webapp/app.py` serving an iterative set-building UI. Shares state with CLI tools via `.sketch_state.json`.

## Run

```bash
python3 webapp/app.py
# http://127.0.0.1:5000
```

## Stop

```bash
pkill -f "webapp/app.py"
```

## Environment gotchas

- **Flask 2.0 + Werkzeug 2.4+ breaks** with `ImportError: cannot import name 'url_quote' from 'werkzeug.urls'`
- Fix: `pip3 install --user "werkzeug<2.4"` (2.3.8 works)
- Don't upgrade Flask without testing — the template is tuned to Jinja 3 / Flask 2.x features

## Shared state pattern

All three processes read/write the same `.sketch_state.json`:
- `set_sketch.py` (CLI) — manual sketch building
- `webapp/app.py` — web UI
- `live_listen.py` — live Shazam-driven appends

No locks, no DB — the JSON file is the source of truth. Single-writer at a time is fine for the dev use case.

Schema:
```json
{
  "started": "2026-04-22T11:15:00",
  "target_hours": 3.0,
  "target_tracks": 54,
  "tracks": [
    {
      "artist": "Pépé Bradock",
      "title": "Deep Burnt",
      "spotify_id": "lfm:abc123...",    // may be real or synthetic lfm:<hash>
      "genre": "house",
      "subgenre": "deep house",
      "rhythm": "four-on-the-floor",
      "texture": "[\"warm/soulful\"]",    // JSON string (legacy)
      "release_year": 1999,
      "identified_at": 1745341234.5,      // optional — set by live_listen
      "picked_fork": "A",                 // optional — which fork, if user picked
      "source": "shazam"                  // optional — 'shazam' | 'manual' | 'cli'
    }
  ]
}
```

## Live-mode detection

The web-app detects "live listener is running" by checking if `tracks[-1].identified_at` is within the last 90 seconds. When live:
- Adds `● LIVE` badge to header
- Injects `<meta http-equiv="refresh" content="10">` so the page updates as new tracks arrive
- Shows hint text pointing to `live_listen.py`

Helper: `_is_live(state)` in `webapp/app.py`.

## Routes

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Show current state + forks. Accepts `?like=<dj1,dj2>` to bias forks. |
| `/seed` | POST | Initialize sketch from seed tracks + hours. Form field `seeds_text` (newline-separated) + `hours`. |
| `/add` | POST | Append a track. Fields: `pick` (A/B/C/D/custom), `artist`, `title`, optional `spotify_id`. |
| `/undo` | POST | Remove last track. |
| `/reset` | POST | Delete `.sketch_state.json`. |
| `/api/state` | GET | JSON dump of current state (for external integrations). |

## Styling notes

- Dark theme by default (values defined in `:root` at top of `webapp/templates/index.html`)
- Fork color coding: blue (parallel), orange (tempo-up), yellow (family-pivot), pink (curtain-drop)
- Monospace digits for numeric columns (font-variant-numeric: tabular-nums)
- No JS framework — vanilla forms, page reloads are the interaction model

## Extension patterns that worked

- **Optional filters as query params**: `?like=dj1,dj2` reads in `index()`, passes through to `get_forks()`. URL is copy-pasteable, shareable, bookmarkable.
- **Collapsible "Available DJs" list**: `<details>` element with the multi-set DJ slugs below the filter input. Better than autocomplete for small datasets.
- **Advisory block colored by severity**: warn=red, nudge=orange, info=gray. Set via inline CSS so Jinja condition is simple.
