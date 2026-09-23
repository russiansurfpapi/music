# MongoDB catalogue (`mongo_catalog.py`, Sept 2026)

A queryable mirror of what is on the Study playlists, one document per track,
carrying its subgenre and every playlist it sits on.

```bash
python3 mongo_catalog.py sync                 # backfill / repair from library.db
python3 mongo_catalog.py stats
python3 mongo_catalog.py find "italo disco"
python3 mongo_catalog.py track <spotify_id>
python3 mongo_catalog.py gaps
```

Current: **15,306 tracks, 15,471 track-playlist links, 87 playlists.**

## library.db is still the source of truth

Mongo is a **mirror, never an origin**. Every field is derived from library.db;
nothing reads Mongo to decide what goes on a playlist. If the two disagree,
library.db wins and `sync` repairs it.

The point is the shape, not the storage: answering "what deep house do I own",
"which playlists is this track on", "what got added this week" means a
three-way join across `tracks`, `classifications` and `playlist_tracks` in a
laptop-local SQLite file. Here it is one indexed lookup, reachable from
anywhere.

Cluster: the same FUM cluster the Cooking repo uses, database `music`.
`MONGODB_URI` / `MONGODB_DB` in `.env` (gitignored).

## Writes hook the chokepoint, not the call sites

`tracklist_scraper._record_membership` is the one function every playlist
membership change passes through, so the mirror hooks there — the same reason
the Spotify budget lives in `spotify_guard`. Policing call sites was tried in
this repo and missed ~90 of them; a chokepoint covers code that does not exist
yet. Removals go through `_forget_from_mongo` and `$pull`.

Mirroring **never blocks the write it mirrors**. Failures are logged, and after
3 consecutive ones it disables itself for the rest of the process rather than
paying a 10s timeout per playlist. That path is not theoretical: the first sync
asked for a column named `year` (it is `release_year`), failed 87 times in a
row and wrote nothing — the guard is what made that obvious instead of slow.

`_TRACK_FIELDS` / `_CLASS_FIELDS` name their columns explicitly and were each
checked against `PRAGMA table_info`. Do not add a field without checking.

## Membership is rebuilt, not merged

`sync` clears `playlists` on every document before re-adding, so a track
removed from a playlist actually disappears instead of lingering forever.
Documents left on no playlist are deleted, so the catalogue only ever
describes what is really out there on Spotify.

## The 1,213-track gap

A track only enters the catalogue if library.db knows its metadata, and
**1,213 IDs sit on Study playlists with no row in `tracks` at all** — added by
a live Spotify sync rather than by the builders, so nothing ever fetched their
artist/title. With no metadata they cannot be classified either, which means
they are invisible to the subgenre playlists as well as to this catalogue.
`mongo_catalog.py gaps` lists them by playlist (Deep House 198, Tech House 127,
Electro 110).

Closing it costs one `sp.track(id)` per ID. `.claude/rules/spotify-api.md`
records that individual `sp.track` calls in bulk are exactly what earned a
10.8-hour ban, so this wants a paced job with the budget guard, spread over
days — not a flag on `sync`.
