# DJ discoveries → subgenre playlists

## What exists
87 Spotify playlists, 16,811 tracks — 71 `<Subgenre> — Study` (13,059) and
16 `<Genre> (Other) — Study` catch-alls (3,752).

Sept 2026: the pool moved from `--source dj-sets` to `--source library`, on
request — a study playlist now holds **everything you own** in that subgenre,
not only what a scraped DJ set surfaced. Deep House went 474 → 2,132.

```bash
python3 build_subgenre_playlists.py --source library --min-tracks 25 --sync
python3 build_subgenre_playlists.py --source library --level genre --min-tracks 25 --sync
```

Both runs together: +11,278 tracks, −136 removed, **349 requests**, no 429.

The removals are `--sync` doing its job — 124 tracks left Instrumental Hip Hop
because they are classified `idm` / `alternative rnb` / `lo-fi` and belong in
those playlists instead. Anything with a genre but no subgenre lands in the
`(Other)` catch-all, which is why the `--level genre` pass is not optional:
without it ~80 of those 124 would sit in no playlist at all.

`DJ Set Discoveries` (`4lIwulyeCrzOPTkDoZFe1l`) is untouched — still the raw archive.

## Rebuild
```bash
cd ~/Documents/music
python3 build_subgenre_playlists.py --min-tracks 10 --sync
```

`--sync` makes each playlist contain *exactly* the selected tracks, removing
anything else. Without it, tracks are only added. Safe to re-run: a playlist
that is already correct costs zero write requests.

## Source of truth: library.db, not the Spotify playlist
`--source` picks the pool:

| value | pool | why |
|---|---|---|
| `dj-sets` (default) | every track in `dj_set_tracks` across all 195 scraped sets | **the right one.** The DB is a superset — it holds 520 more classified discoveries than ever landed on the Spotify playlist |
| `playlist` | the IDs on `DJ Set Discoveries` right now, via `discovery_playlist_ids.json` | lossy subset; refresh with `python3 snapshot_discovery_playlist.py` |
| `library` | every classified track you own | **not discoveries.** Produced 79 bloated playlists on the first attempt |

`--min-tracks` is applied *after* Spotify-ID resolution, so it counts tracks
that actually reach the playlist. 6 subgenres sit just under 10 and are skipped:
Neo-Soul 9, Hyperpop 9, Footwork 8, Funk Carioca 7, Future House 6, Tribal House 6.

## Never hitting the rate limit again

Three 24h `QUOTA_EXCEEDED` bans in this project. The cause was never one greedy
loop — it was many polite scripts re-reading state the DB already held, with no
shared accounting.

### 1. A real ceiling (`spotify_budget.py`)
Every Spotify request in the repo funnels through `tracklist_scraper._spotify_call`,
which now `charge()`s the daily budget first. Over the limit and the call is
never made. Counting is in-memory with a periodic flush, and the flush swallows
a locked DB — accounting must never break the work it accounts for.

```bash
python3 spotify_budget.py               # requests today / limit / cooldown
SPOTIFY_DAILY_LIMIT=5000 python3 ...    # raise it deliberately
```

### 2. A remembered ban
A 429 with a long Retry-After writes `cooldown_until` to the DB. Later runs
refuse before calling, so re-running during a ban costs **zero** requests
instead of one per attempt. It reads as a message, not a stack trace:

```
STOPPED: Spotify is rate-limiting this account until 2026-08-27T16:58:34.
         No requests will be made. Re-run after that time.
  spent today: 2/2500
```

### 3. The guard cannot be bypassed
`_spotify_call` was never the chokepoint it claimed to be — **~90 direct
`sp.search()` / `sp.playlist()` calls across 19 scripts never went through it**,
and 8 scripts build their own client without touching `auth.py`.

`spotify_guard.py` patches `spotipy.Spotify._internal_call`, the one function
every spotipy request passes through, on every instance regardless of who
constructed it. Importing the module is enough. `check_budget_guard.py` fails
if any file imports spotipy without it, so future scripts cannot regress:

```bash
python3 check_budget_guard.py    # "all spotipy users are budget-guarded"
```

The ceiling also learns: a ban records the spend at which it landed, and the
limit drops to 80% of the lowest observed ban point.

### 4. Stop re-reading what we already store
| was | now |
|---|---|
| playlist index = 17 pages per build process | read from `playlists` (0 requests) |
| membership = 1 page per 100 tracks, per playlist, every run | skipped when `snapshot_id` is unchanged |

`snapshot_id` is the key: Spotify changes it iff the contents changed, and it
arrives free in the catalogue page. One ~17-request catalogue refresh now tells
every playlist whether it needs reading at all.

Measured on the real workload:

| operation | before | after |
|---|---|---|
| `sync_playlists.py` | 287 | **11** |
| `build_subgenre_playlists.py --sync` | ~130 | **12** |

A stale-cache miss still verifies live before creating anything, so the savings
never risk a duplicate playlist.

### 5. Pagination bug that was corrupting reads
Every paginator broke on `len(items) < page_size`. **Spotify returns 49-item
pages mid-listing**, so this stopped early and silently truncated results:
the playlist catalogue read 547 of 1,546. A truncated index makes an existing
playlist look absent — which is how duplicates get created — and a truncated
`playlist_existing_ids` makes an incomplete dedupe set, which is how tracks get
double-added. Fixed in 6 files; all paginators now trust `next`.

## Schema: one database, full provenance

```
tracks              19,646   metadata (album, year, duration, artist_id)
classifications     18,152   genre / subgenre
track_tags         158,283   Last.fm tags
dj_sets                195   scraped sets: dj, title, date, source file
dj_set_tracks        6,940   set -> track, with position + raw AND normalised names
track_sources       22,097   track -> origin (liked | recent | dj_set:<dj>)
playlists              842   every Spotify playlist, managed flagged
playlist_tracks     19,541   which tracks are on which playlist
spotify_id_cache     4,098   artist/title -> id (NULL = known miss)
track_provenance    21,518   VIEW: one row per (track, where it came from)
```

**Every track is attributable.** 3,144 tracks had no `track_sources` row even
though `dj_set_tracks` knew their origin — `backfill_provenance.py` fixed that;
1 mojibake track remains.

`track_sources` collapses a track to one row per DJ. The `track_provenance`
view keeps the detail: which set, what date, what position, and how the artist
was spelled on that tracklist. A track played in 8 sets gets 8 rows.

```bash
python3 track_story.py "bla bla bla"     # metadata + every set + every playlist
python3 sync_playlists.py                # refresh managed playlist membership
python3 sync_playlists.py --list-only    # just the catalogue, ~17 requests
python3 backfill_provenance.py           # rebuild track_sources from the sets
```

`sync_playlists.py` splits cheap from expensive on purpose: the catalogue costs
~17 requests for 842 playlists; membership costs a page per 100 tracks per
playlist, so it defaults to the ~185 managed ones. `--all` exists but is slow.

## One database
`library.db` is the single source of truth. Pipeline state used to live in JSON
files beside it, written by six scripts with no single owner — a crash could
leave them disagreeing with the DB.

| was | now |
|---|---|
| `spotify_cache.json` | `spotify_id_cache` (`spotify_id NOT NULL`) |
| `spotify_search_misses.json` | `spotify_id_cache` (`spotify_id IS NULL`) |
| `discovery_playlist_ids.json` | `playlist_snapshots` |

Retired copies are in `archive/json-sidecars-retired-20260825/`.

`_load_spotify_cache()` still returns a dict-like object, so no call site
changed — but assigning a key now writes through to the DB immediately, which
is what makes a mid-run rate-limit ban non-destructive. `_save_spotify_cache()`
is a no-op kept for compatibility.

## Scrape-time normalisation (the root fix)
`dj_ingest.extract_set_tracks()` normalises at the boundary, so nothing
downstream has to guess. `dj_set_tracks` now carries both:

- `raw_artist` / `raw_title` — exactly as scraped, never rewritten (audit trail)
- `artist` / `title` — normalised; **3,210 of 6,940 rows (46%) differed**

`dj_stub_tracks.py`, `backfill_missing_tracks.py` and `lastfm_tags.promote_orphans`
all read `COALESCE(artist, raw_artist)`, so mangled names no longer propagate
into `tracks` and poison Last.fm lookups.

Backfill older rows with `python3 backfill_normalized_names.py [--force]`.

## Coverage pipeline
Only 34% of DJ-set tracks reached a playlist on the first pass. The funnel:

```
5,093  DJ-set tracks in library.db
3,912  got a classification row
2,839  got a SUBGENRE            <- 1,073 stop at a parent genre
1,905  resolve to a Spotify ID   <- 809 synthetic IDs with no cache hit
1,755  actually on a playlist    <- 150 lost to --min-tracks 10
```

Three tools close those leaks:

| script | leak | notes |
|---|---|---|
| `refetch_missing_tags.py` | 1,184 tracks Last.fm had no tags for | ~40% recovery. No Spotify cost. |
| `resolve_synthetic_ids.py` | 809 classified tracks with no Spotify ID | ~98% hit rate. **Spends Spotify quota — batch it.** |
| `--level genre` | 1,073 tracks stuck at a parent genre | builds `<Genre> (Other) — Study` catch-alls |

### artist_normalize.py
Two scraper defects made Last.fm return nothing:
- features concatenated without a separator — `Disclosureft. Eliza Doolittle`
- diacritics are NOT autocorrected — `RÜFÜS DU SOL` returns 0 tags, `Rufus Du Sol` returns them

`artist_candidates()` yields progressively looser spellings; `clean_title()` strips
remix/edit suffixes (with no `\b` before `remix`, since the scraper also concatenates
those: `(D-Nox & BeckersRemix)`).

The real fix is to normalise `raw_artist` at scrape time so this stops happening.

## Known gaps
- **92 synthetic-ID tracks still unsearched** — finish with
  `python3 resolve_synthetic_ids.py --limit 100` once the quota resets.
- **709 DJ-set tracks have no Last.fm tags even after normalisation**, and 938
  sit at a parent genre with no subgenre available. Not retryable — Last.fm
  genuinely has nothing for obscure white-label and edit-only tracks. Going
  further needs a different tag source (Discogs, Beatport, Spotify artist genres).
- **15 leftover playlists from the first attempt** are pure library content and
  were deliberately left in place: Alternative R&B, Neo-Soul, Instrumental Hip
  Hop, Footwork, Jazz Rap, Nu-Disco, Pop Rap, Afrobeats, Art Pop, Cloud Rap,
  Outsider House, Drill, Funk Carioca, Trap Latino, UK Rap. They share the
  `— Study` suffix but are **not** discovery playlists.

## Spotify API gotchas (learned the hard way)
- Playlist items nest the track under `item`, not `track`. `fields="items.track.id"`
  returns `{}` per entry — a silently empty dedupe set that re-adds everything.
  Always request `items(item(id),track(id))` and accept either.
- `sp.playlist(id, fields='tracks.total')` → KeyError; the full response puts
  items at the top level, with no `tracks` wrapper.
- Batch `GET /tracks?ids=` returns **403** on this app (same restriction as
  audio-features). Single `/tracks/{id}` works.
- `_get_or_create_playlist` used to page all 1,534 playlists per call and
  triggered a 23-hour `QUOTA_EXCEEDED` ban. Now indexed once per process.


## The wrong-track bug (fixed Sept 2026)

`find_spotify_track` accepted `items[0]` from a Spotify search without checking
the hit was the track asked for. Spotify ranks *something* first for every
query, so when a tracklist named an obscure remix it does not carry, the cache
learned garbage as truth and served it to every later run.

**158 of 2,687 checkable cache entries (6%) pointed at the wrong track.**

| tracklist said | cache stored |
|---|---|
| High Contrast — Remind Me (Jungle Mix) | Kid Cudi — Maui Wowie |
| Disclosure — What's In Your Head (VIP) | The Cranberries — Zombie |
| Vril — Voices In Your Head | Laura Branigan — Gloria |
| 2Pac — California Love (James Hype) | The Game — Freedom |

The damage was to *provenance*, not metadata: `tracks`, `track_tags` and
`classifications` all described the real track correctly — Kid Cudi really is
hip-hop. What was false was the claim a DJ played it. Those tracks inherited
`dj_set:*` sources and a seat on DJ Set Discoveries, while the track actually
played was lost.

### Detection is free
`tracks` already stores real Spotify metadata for every cached ID, so the cache
key can be checked against it with **zero API calls**. Artist agreement is the
signal — titles legitimately differ by remix suffix (`Stranger(DJ BORINGremix)`
vs `Stranger - DJ BORING Extended Mix`), artists do not.

```bash
python3 repair_cache_mismatches.py            # report
python3 repair_cache_mismatches.py --write    # apply
```
Cleared 158 cache entries, 138 `dj_set_tracks` links, 427 false `dj_set:`
sources. `tracks` / `classifications` / `track_tags` are never touched.

### The fix that stops it recurring
`find_spotify_track` now pulls 5 candidates and takes the first that passes
`_is_same_track` (artist tokens overlap, or a strong title agreement for
tracklists that credit the remixer), **preferring an exact title match** — so
a request for `Hell suite, Pt. I` can no longer settle for `Pt. II` just
because the artist agrees. That sibling-track case is the one artist-level
verification alone does not catch.

## Pacing (Sept 2026)
`spotify_guard` now enforces a **0.75s floor between every request** (~40 per
30s window against a ~50 limit) and a 30s breather every 200 requests. It lives
beside the budget charge for the same reason the budget does: that is the one
function every spotipy request passes through, so a script that never heard of
it is still paced. Sleeping in call sites was tried and missed the ~90 direct
`sp.foo()` calls across the repo.

```bash
SPOTIFY_MIN_INTERVAL=1.5 python3 ...   # slower
SPOTIFY_BREATHER_EVERY=0 python3 ...   # disable the breather
```

## Four more instances of the same two bugs (Sept 2026)

Auditing for the wrong-track bug found it in three more places, plus the
truncating paginator the earlier sweep missed.

| file | defect | why it mattered |
|---|---|---|
| `resolve_synthetic_ids.py` | unverified `items[0]` written to `spotify_id_cache` | this is the script the docs tell you to run for the 809 unresolved IDs — running it would have re-created the 158-entry corruption wholesale |
| `resolve_dj_tracks.py` | tried artist overlap, then `return items[0]["id"]` anyway | writes to both `spotify_id_cache` and `dj_set_tracks` |
| `artist_influences.py` | `return items[0]["id"]` after exact + substring miss | attributed influences to whoever Spotify ranked first |
| `pull_library.py` | `if len(items) < PAGE or next is None` | the documented short-page bug, still live here — silently truncated the library pull |

All four now use `_is_same_track` or return `None`. The ISRC lookup in
`resolve_dj_tracks.py` still takes `items[0]` and is *correct* — an ISRC is a
unique recording identifier, so the first hit is the only hit.

Still unverified, lower blast radius, not fixed: `virgil.py` (checks artist
*or* title, so a title-only match can return the wrong artist) and
`1003scraper.py` (searches a combined string with no artist/title split, so
there is nothing to verify against).

## Membership was silently not recorded for new playlists

`playlist_tracks` has a foreign key to `playlists`, and `_get_or_create_playlist`
created the playlist on Spotify without inserting the `playlists` row. So
`_record_membership` raised `IntegrityError` straight into a bare `except` and
the playlist kept **zero** membership rows. `track_count` still looked right,
because a later catalogue refresh filled it from Spotify's own count — which is
what made this invisible.

**14 of 87 study playlists were in that state**, which is most of what looked
like 1,009 uncategorised tracks. It was a reporting artifact: the tracks were on
Spotify the whole time.

- `_get_or_create_playlist` now registers the row at creation.
- `_record_membership` still never raises, but logs a warning instead of
  swallowing in silence.
- Backfilled the 14 from the same DB query that built them — no API calls.

Orphans went 1,009 → 369, and all 369 are subgenres genuinely under
`--min-tracks 25` (ballroom 24, tropical house 22, garage house 20 …).
