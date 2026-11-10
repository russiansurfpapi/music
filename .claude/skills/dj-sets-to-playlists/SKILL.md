---
name: dj-sets-to-playlists
description: Turn a DJ's YouTube sets into Spotify playlists — one playlist per set AND every set merged into "<DJ> — All Sets", always both. Finds the sets, fingerprints them with ACRCloud, matches the songs on Spotify within the daily limit, builds the playlists, and resumes after a Spotify cooldown. Use when Sasha names one or more DJs and asks for their sets as playlists, "fingerprint X's sets", "make playlists for these DJs", "finish the music stuff", or "merge them by DJ".
---

# DJ sets → Spotify playlists

**Every run ends with both kinds of playlist, for every DJ named:**
1. one playlist per set, named after the set (sets with at least 3 songs found on Spotify), and
2. **"<DJ> — All Sets"**: every set merged, oldest-first as listed, songs in set order, each song once.

Never stop after the per-set playlists. If Spotify blocks the run before the merge, the merge is the first thing
to do once the block lifts (Sasha, 2026-11-10: "merge them by dj too, the skill should always do it").

## The command (one per DJ)

    cd ~/Documents/music
    /usr/bin/python3 dj_sets_playlist.py "Benji B" --plan          # find the sets, print ACRCloud cost, stop
    /usr/bin/python3 dj_sets_playlist.py "Benji B" --yes           # everything: find → identify → match → playlist
    /usr/bin/python3 dj_sets_playlist.py "Benji B" --from match    # after a Spotify cooldown
    /usr/bin/python3 dj_sets_playlist.py "Benji B" --only playlist # rebuild the set playlists + All Sets only

- Use `/usr/bin/python3` (3.9, where spotipy/requests live); Homebrew `python3` lacks them.
- Several DJs: run them one after another (not in parallel — they share one Spotify allowance), in the background
  with `nohup caffeinate -i ... & disown`, and log to `.runs/<slug>/run.log`.
- The playlist stage is safe to rerun: playlists are found by name and only missing songs are added.

## Limits that decide the pace

- **Spotify:** about 600–780 requests a day before an 11-hour block (`python3 spotify_budget.py` shows today's count
  and any cooldown). Searches are 5 s apart; 1.5 s earned a block. Matching stops cleanly on the limit; rerun
  `--from match` the next day, then `--only playlist`.
- **ACRCloud:** one lookup per 60 s of audio (~90 per 90-minute set). Show the `--plan` cost before spending.
- A set counts as identified at 80 % of its chunks answered; don't re-fingerprint it to chase 100 %.

## What to report

Per DJ: sets found, songs identified, songs on Spotify, the "All Sets" link with its size, and how many set
playlists were made. Name what is left (songs not yet searched, the date Spotify's block lifts).

Details and history: `.claude/rules/audio-fingerprinting.md` (section "One artist -> one All Sets playlist").
