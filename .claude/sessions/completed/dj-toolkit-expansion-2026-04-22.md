## Resume command: claude --resume --dangerously-skip-permissions
## Date: 2026-04-22
## Status: in-progress

---

# WHAT WAS DONE

## Classification accuracy overhaul (major)
- Executed the plan at `.claude/plans/delegated-mapping-nest.md` (now stale — work done)
- Backed up DB to `library.db.bak` (before) and `library.db.bak-2026-04-22` (after today's session)
- `tag_map.yaml`: removed meta-tags (`electronic`, `dance`, `edm`, `electronica`, `club`) from `genre.house`; added 19 missing subgenres (nu-disco, cosmic disco, speed garage, two-step, gqom, peak time techno, hard techno, psytrance, goa trance, progressive trance, liquid dnb, neurofunk, jump up, microhouse, brostep, ballroom, jersey club, funk carioca, dembow); moved `microhouse` out of `minimal techno`; moved `hard techno` out of `industrial techno`; added `progressive`/`progressive electronic` patterns to `progressive house`; removed overlapping `jersey club` from `baltimore club`
- `classify.py`: added `SUBGENRE_TO_GENRE` fallback (backfills genre from subgenre when genre score is 0), `SUBGENRE_DNA` inference (+15 to production_dna for canonical lineages like chicago_house→909, drill→808), `_disambiguate()` for ambiguous single-word tags (`minimal` + house cues → suppress minimal-techno; `industrial` + hip-hop cues → suppress industrial-techno; `progressive` + trance cues → boost progressive-trance over progressive-house)
- Reclassified 15,226 tracks. Genre=house went 6,029 → 3,911 (-35%). house+footwork cross-contamination 87 → 19. 909 DNA 0 → 1,018. New subgenres populated 0 → 340 tracks.

## The ID-placeholder bug (high-impact discovery)
- 204 `dj_set_tracks` rows with `raw_artist='ID', raw_title='ID'` (1001TL's unidentified-track convention) all soft-linked to ONE real Spotify track: `5nrbuDZe5Hod1nUsND6QUA` — an actual artist literally named "ID" who makes progressive trance
- Every DJ's "unidentified" tracks were being falsely classified as progressive trance. Mochakk showed 44 prog-trance tracks (42% of his set!) — all contamination. Shadow Child showed 54.
- Fix 1 (data): `UPDATE dj_set_tracks SET spotify_id=NULL WHERE LOWER(raw_artist)='id'`; `DELETE FROM classifications/track_tags/tracks WHERE spotify_id='5nrbuDZe5Hod1nUsND6QUA'`
- Fix 2 (cache): purged 4 bogus `id||*` keys from `spotify_cache.json`
- Fix 3 (code): added `_is_unidentified(artist, title)` guard in `resolve_dj_tracks.py` cache + search paths; added ID-skip in `dj_archetype.py` signature/opener/closer detection. Affected set_id list: {`id`, `i.d.`, `unknown`, `?`}
- Post-fix: Shadow Child's prog trance dropped 54→5 (real: Age of Love, Pete Lazonby, Sasha "Ether" survived as legit classics)

## Shell-HTML quarantine (high-impact discovery)
- Audited all 163 HTMLs in `sets/`: **76 were hollow Cloudflare-homepage redirects** (0 `.trackValue` elements, generic title)
- Moved to `sets_broken/`. Deleted 22 zombie `dj_sets` rows that had been ingested from them with track_count=1 (fake boilerplate match).
- Fixed `tracklist_scraper.py:fetch_and_save()` to refuse saving HTML where `'class="trackValue"' not in html` AND title contains `"1001Tracklists ⋅ The World's Leading"`.
- Pattern: these shells come from the Playwright scraper after Turnstile redirects the fetch to the 1001TL homepage. Don't scrape listed URLs in sets_broken/ again.

## Slug consolidation (many rounds)
- Ingest parser splits DJ names at first 2 tokens when a 3rd-plus token follows (e.g., "sven-vath-at-cocoon..." → `sven-vath`; but "mochakk-circoloco-dc10-..." → `mochakk-circoloco`). Led to fragmented slugs.
- Consolidated in SQL across the session: `sven`→`sven-vath`, `annie`→`annie-mac`, `tycho-dusty`→`tycho`, `kolsch-ipso`→`kolsch`, `sasha-aria,sasha-warung`→`sasha`, `kink-further,kink-fact,kink-boiler`→`kink`, `mochakk-*`→`mochakk`, `jackmaster-*`→`jackmaster`, `the-martinez`→`martinez-brothers`
- Clean-up of `_html`-suffixed set_ids (old ingest artifact): 141 → 0 after dedupe (94 had new counterparts = safe delete; 1 orphan renamed; 46 were 0-track shells = deleted)

## Tool suite built this session

| Tool | Lines | Purpose |
|---|---|---|
| `set_shapes.py` | ~280 | One-char-per-track timeline strip. Shape codes: PLATEAU/BLOCKS/WAVE/ECLECTIC/MIXED. Transition rate, family-crossings, quintile tops. |
| `set_energy.py` | ~220 | BPM + texture-energy sparkline per set. BPM from `SUBGENRE_BPM` midpoints (no track-level BPM since Spotify audio features are 403'd). |
| `transition_atlas.py` | ~260 | Move catalog. Subgenre→subgenre observed transitions, grouped by PARALLEL/TEMPO_UP/TEMPO_DOWN/FAMILY_PIVOT/CURTAIN_DROP with BPM deltas. |
| `next_move.py` | ~270 | Position-aware recommender with citations. `--per-dj` flag for side-by-side DJ panels (2-3 directions each). |
| `set_planner.py` | ~230 | Given start + hours + optional `--like`/`--venue`, generate quintile-by-quintile composition + transition suggestions + curveball slot. |
| `set_sketch.py` | ~400 | Iterative fork-based set builder. State in `.sketch_state.json`. 4 forks per turn (A/B/C/D), plateau/family-run/dominance/quintile advisories, `--like <djs>` filter. |
| `space_miami_analysis.py` | ~150 | Venue-specific anatomy: arc by quintile, canon (tracks ≥2 DJs play), per-DJ subgenre composition, per-DJ curveballs. |
| `import_downloads.py` | ~240 | Auto-ingests HTMLs from ~/Downloads: verifies real tracklist (not shell), normalizes filename, DB-aware dedup (dj-first-token + date), runs full pipeline including Last.fm bypass for unresolved tracks. |
| `webapp/app.py` + `templates/index.html` | ~250 + ~260 | Flask web-app for set_sketch. Shared `.sketch_state.json` with CLI. DJ filter, plateau advisories, live-mode badge, 10s auto-refresh. |
| `live_listen.py` | ~240 | Live audio identification via Shazam (ffmpeg avfoundation → shazamio → library lookup → state append). |

## Documentation / analysis outputs generated
- `DJ_ARCHETYPES.md` — 30 DJ fingerprints (5 axes + vibe cloud + interview quotes + vocab + emulation tips)
- `SPACE_MIAMI.md` — 16-set anatomy
- `SPACE_MIAMI_SHAPES.md` — 13-set timeline strips
- `SPACE_MIAMI_ENERGY.md` — BPM/energy sparklines
- `TRANSITION_ATLAS.md` — full cross-DJ move catalog
- `SETUP.md` — onboarding doc for collaborators

## Subagents
- `.claude/agents/dj-demo.md` — live demo guide for showing the toolkit to a friend (Maygong scope)
- `.claude/agents/dj-scraper-planner.md` — audits DB coverage, finds 1001TL URLs via MCP/WebSearch, tries auto-scrape in cost order, falls back to manual instructions

## New interview integrations
- Parsed Cyril Hahn quotes from Complex UK + Wonderland Magazine → appended to `dj_interviews_raw.md`
- Added 4 vocab terms under `subgenre_usage` in `dj_interviews_vocab.yaml` (`mellow steady kick drums (accidental deep house)`, `chopping up accappellas`, `melody-first mellow stuff`, `vocal hooks as selection criterion`)
- Fixed YAML parse bug at line 255 (unquoted parentheses in `quote` field)

## New DJ scrapes ingested this session
- Duke Dumont: 7 sets (scraped via Playwright+BD, 7/10 succeeded)
- Mochakk: 4 sets (manual browser save — all Playwright retries failed)
- Peggy Gou: 1 set (Lost Village 2020, 31 tracks)
- Space Miami batch: A-Trak, ANOTR+Michael Bibi, BLOND_ISH, Carl Cox, Carlita, Disco Lines, Gorgon City, Hayden James, James Hype, Jayda G, Maceo Plex, Nina Kraviz, Oscar G, Rebūke (14 sets via manual save + `import_downloads.py`)
- Diplo: 6 sets including Higher Ground, Pacha, Burning Man, Ritvales
- Jackmaster: 5 sets (Boiler Room, Mastermix, RA, Numbers Warehouse, CRSSD) — 4.74 entropy, highest in dataset
- Jamie xx: 6 sets (Essential Mix, Boiler Room Reykjavik, HISTORY Toronto, Lot Radio, etc.) — 60% non-4/4 rhythm, 3× anyone else
- Moodymann: 3 sets (RA Podcast w/ Carl Craig + Mike Banks, RBMA Rollerskating, GTA Online)
- Martinez Brothers: 6 sets (Printworks, Hï Ibiza, LA Coliseum, Re_Frame Studios, Horse Park, DJ Mag MMW)
- Cyril Hahn: 1 manual 11-track Pjanoo mix (inserted directly via SQL, no HTML)

Final DB counts: **94 DJs, ~120 sets, ~2,200 dj_set_tracks, 16,458 classifications**

## Web-app + ACRCloud setup
- Flask 2.0 + werkzeug 2.3.8 (downgraded from 2.4 to fix `url_quote` ImportError)
- Web-app running at http://127.0.0.1:5000 (PID visible via `ps aux | grep webapp/app.py`)
- ACRCloud trial account registered — creds in `.env` (`ACR_HOST=identify-us-west-2.acrcloud.com`, `ACR_KEY=29befde9...`, `ACR_SECRET=3aanO2D...`). 14-day trial, 3000 requests.
- `identify_youtube_set.py` (built earlier in session — other terminal session, not this one) used for YouTube set identification

## Files read (key line numbers noted)
- `tag_map.yaml` (full)
- `classify.py` (full)
- `dj_profiles.py` (full)
- `dj_archetype.py` (various edits — `_vibe_tag_cloud`, `_interview_quotes`, `_interview_vocab`, `compute_axes`, `fmt_profile`, `main`)
- `ingest_sets.py` (~lines 39-65 for parse_filename)
- `tracklist_scraper.py` (~lines 92-200 for fetch_html/fetch_and_save; ~lines 730-800 for main)
- `resolve_dj_tracks.py` (~lines 27-80 for cmd_cache; ~lines 120-170 for cmd_search)
- `identify_youtube_set.py` (full)
- `dj_interviews_vocab.yaml` (various line ranges)
- `requirements.txt`

## Files created
- `set_shapes.py`, `set_energy.py`, `transition_atlas.py`, `next_move.py`, `set_planner.py`, `set_sketch.py`, `space_miami_analysis.py`, `import_downloads.py`, `live_listen.py`, `webapp/app.py`, `webapp/templates/index.html`
- `DJ_ARCHETYPES.md`, `SPACE_MIAMI.md`, `SPACE_MIAMI_SHAPES.md`, `SPACE_MIAMI_ENERGY.md`, `TRANSITION_ATLAS.md`, `SETUP.md`
- `.claude/agents/dj-demo.md`, `.claude/agents/dj-scraper-planner.md`
- `library.db.bak-2026-04-22` (DB snapshot)

## Files modified
- `tag_map.yaml`, `classify.py`, `tracklist_scraper.py`, `resolve_dj_tracks.py`, `dj_archetype.py`, `dj_interviews_raw.md`, `dj_interviews_vocab.yaml`, `README.md` (SETUP pointer)

---

# WHAT WAS LEARNED

## Last.fm taxonomy gap — soulful house folds to deep house
- Community tagging is coarse: `soulful house`, `jackin house`, `fidget house`, `garage house`, etc., rarely have explicit tags. Most fold into generic `deep house` or `house`.
- Consequence: `next_move.py --from "soulful house"` yields 1 citation; `--from "deep house"` yields 228.
- Workaround: use `deep house` as the coarse aesthetic, treat soulful/disco house as a texture/vibe layer. Or upgrade classification with Phase C (artist-anchor based: Kerri Chandler, Larry Heard, Frankie Knuckles = soulful house boost).

## The ID-contamination pattern (keep this in mind for similar dirty-joins)
- Whenever a placeholder artist name ("ID", "Unknown", "?") gets fuzzy-matched, it ALWAYS wins against some real artist somewhere on Spotify. One-to-many mapping poisons every downstream metric.
- **Lesson**: ALWAYS have a `_is_unidentified()` filter before fuzzy-matching in any scraping/resolution code. Even if the raw-artist string passes through, soft-linking it to a real entity is the actual harm.
- Where to apply: `resolve_dj_tracks.py`, any future Shazam/ACR resolver, any classification join.

## Shell-redirect pattern recognition
- Cloudflare Turnstile doesn't return a 403/blocked page. It serves the generic 1001TL homepage HTML (~60-80KB, title "1001Tracklists ⋅ The World's Leading DJ Tracklist/Playlist Database", 0 `.trackValue` elements).
- These are indistinguishable from legitimate HTML by size alone. Must check content structure (`.trackValue` count) before believing a scrape worked.
- The `fetch_and_save()` guard added to `tracklist_scraper.py` now prevents this from re-happening. Any future ingest should check `tv_count > 0` before persisting.

## Spotify sustained-rate ban mechanics
- Dev-mode instantaneous limit: ~50/30s.
- Undocumented sustained quota: triggers after ~300-400 cumulative calls across few hours.
- Observed Retry-After: **38,929 seconds (~10.8 hours)**. The ban is per-account (not per-app).
- Combining `resolve_dj_tracks.py search` (191 calls at 5s) with `dj_tracks_upsert.py` (starts individual sp.track calls at 1.5s) = triggers the sustained ban in ~20 min.
- **Workaround**: the `lfm:<md5-hex-16>` synthetic spotify_id convention. Insert into `tracks` with synthetic ID; `lastfm_tags.py` looks up by `(artist, title)` not `spotify_id`, so enrichment works; `classify.py` keys on spotify_id but doesn't care if it's real; `dj_set_tracks.spotify_id` gets updated to the synthetic. Later, when Spotify quota recovers, `resolve_dj_tracks.py search` can upgrade these to real IDs.

## MCP Bright Data tools: unknown reliability
- Tried `mcp__Bright_Data__scrape_as_markdown` on Mochakk URL → "internal error"
- User cancelled subsequent test calls before they fired — current status is unknown
- Manual browser save path (Cmd+S → Webpage HTML Only → drop in `~/Downloads`) is 100% reliable and takes ~1 min/set
- If MCP is investigated again: prefer `scrape_as_markdown` on individual tracklist URLs; check if output contains `.trackValue`-equivalent markdown before believing result

## The "median run = 1" insight
- Across 119 sets and 94 DJs, almost every DJ has median run length = 1 (they change subgenre every track).
- Even "tunnel-diver" Dubfire-loveland (minimal techno 55%, entropy 1.57) has median run 1.
- The difference between tight and wide DJs isn't duration-in-lane, it's **count of distinct lanes** per set. Tunnel-divers visit 4 lanes; eclectics visit 16+.
- Plateau warnings fire at run ≥3 (nudge) or ≥4 (warn) — both are genuinely unusual.

## Artists vs tracks: what "eclectic" actually means (Moodymann surprise)
- Initial hypothesis (from his Mixmag/RA rep): "Moodymann plays wildly eclectic curveballs"
- Data says: archetype = `disciplined · quick-hop` (entropy 2.77, deep house 46%). His "curveballs" (Detroit techno, disco-funk-soul, neo-soul, hip-hop) are **regular rotation**, not outliers.
- Contrasts with Jamie xx (`eclectic · self-promoting · historical · rhythmically-adventurous`) who's the opposite: dubstep anchor (85% of arena set), curveballs everything else.
- Lesson: "eclectic" and "curveball-heavy" are different things. Need to measure them separately.

## Cloudflare Turnstile is URL-specific fingerprinting
- Duke Dumont URLs: 7/10 cleared via Playwright+BD
- Mochakk URLs: 1/8 cleared (the 1 was sassy-b-ultravegas wrongly matched by Google search)
- Not random — specific URL slugs get harder over time. Datacenter proxy IPs especially fingerprinted.
- Upgrade path: BD Scraping Browser ($5/GB) solves Turnstile natively, or manual browser save. Residential BD zone helps but costs more.

## DB schema quirk — djs.display_name vs djs.name
- The `djs` table column is `name`, not `display_name`. identify_youtube_set.py initially had `INSERT INTO djs (slug, display_name)` which errored. Fixed by swapping to `name`.
- Schema is: `djs(slug TEXT PRIMARY KEY, name TEXT NOT NULL, is_favorite INTEGER DEFAULT 0, notes TEXT)`.

## Web-app patterns that worked
- **Shared `.sketch_state.json`**: CLI (`set_sketch.py`), web-app (`webapp/app.py`), and live listener (`live_listen.py`) all read/write the same JSON file. No web-service or message-queue needed.
- **Live-mode detection**: check if `tracks[-1].identified_at` is within last 90s. If yes, show `● LIVE` badge + inject `<meta http-equiv="refresh" content="10">`.
- **Flask debug mode** (`app.run(debug=True)`) auto-reloads on code edit — smooth iterative dev.

---

# WHAT IS PENDING

## Still-running services
- Flask web-app at http://127.0.0.1:5000 (PID from `ps aux | grep webapp/app.py`). Kill with `pkill -f "webapp/app.py"`.
- No other background processes left open as of end of session.

## Exact next step on resume
**The user's last ask was:** wire ACRCloud into `live_listen.py` as an alternative backend, and/or add keyboard shortcuts for fork-picking in live mode.

Most obvious next step: add `--backend acrcloud` flag to `live_listen.py` that reuses `acr_identify()` logic from `identify_youtube_set.py` (single-chunk variant). Use the creds already in `.env`.

Also pending:
- BlackHole not installed — only MacBook Pro mic is available for live capture. `brew install blackhole-2ch` to unlock clean system audio.
- `identify_youtube_set.py` background run may or may not have finished — check `dj_set_tracks` for set_id=`ePJzsMgWOy8` to see if the DJ Seinfeld b2b Demi Riquísimo set ingested.

## Unanswered questions
- Does `mcp__Bright_Data__scrape_as_markdown` actually work on 1001TL? Never confirmed — user kept cancelling the approval prompt. Tempting to test with one call next session.
- Does Spotify quota recover after 11 hours? Ban was triggered ~10 AM 2026-04-21. Should be clear by now. User could try `python3 resolve_dj_tracks.py search` to upgrade `lfm:<hash>` IDs back to real Spotify IDs — but do it sparingly.
- The `mochakk` profile shows BPM curve spike to 170 BPM at track 4 of James Hype's 129-track set — is this a classification mishit? Track 4 classified as something at 170 BPM (jungle/dnb?) that seems wrong for an opener. Worth spot-checking.

## Tasks in task list not yet done
- #11 DJ vocab — Phase B: interview extraction. Cyril Hahn done; Jamie xx, Jackmaster, Moodymann, Martinez Brothers, Peggy Gou all have WebSearch candidates but nothing extracted yet.
- #12 DJ vocab — Phase C: apply to classifier. Design sketch in my head: use `used_by` DJ → artist roster mapping + vocab term → track-tag scoring boost. E.g., Moodymann's `Motor City-inspired soulful house` term → +15 to `deep house` when tags include `detroit` OR artist in {Amp Fiddler, Theo Parrish, Kyle Hall, Rick Wilhite}.

## Known-busted or suspect
- `set_sketch.py` recommendations still show some weird matches in fork candidates (e.g., a "rock" pick had "Kidnap, Chambray — Sea Breeze" — not rock). Classifier false-positives — not a tool bug, a taxonomy weakness.
- Web-app "custom add" form takes artist+title but can't pass a Spotify ID through — so library lookup is fuzzy, may miss. Fine for typical use.
- The web-app's "Bias forks to DJs" text field expects comma-separated slugs — no autocomplete. The `<details>` dropdown lists all multi-set DJs for reference.

---

# CRITICAL CONTEXT

## Key architectural decisions
1. **Last.fm-only bypass is a first-class path, not a workaround.** With Spotify dev-mode bans being routine, the `lfm:<hash>` synthetic ID pattern + direct Last.fm enrichment is now the default for new track ingests. Only upgrade to real Spotify IDs when building playlists or when the user explicitly invokes `resolve_dj_tracks.py search`.

2. **Shared state file (.sketch_state.json) is the hub.** CLI, web-app, live listener all read/write it. Keeps coupling low while enabling real-time UX across processes.

3. **Subgenre-level BPM bands are good enough.** Track-level BPM via Spotify audio features is 403'd in dev mode. Using `SUBGENRE_BPM` midpoints (e.g., deep house = 115-122, average 118) is the proxy. Loses precision but enables all the BPM-delta analysis (transition_atlas, set_energy, set_planner).

4. **Shell-redirect guard is mandatory at two layers.** (a) `fetch_and_save()` refuses to persist shells. (b) `ingest_sets.py` ignores 0-track HTMLs. Together they make pipeline safe against Turnstile redirects.

5. **Manual save → `import_downloads.py` is the documented fallback.** When automated scraping fails (Mochakk URLs, Turnstile fingerprint on specific hashes), the user opens 1001TL in Chrome, Cmd+S → drops HTML in `~/Downloads`, runs `python3 import_downloads.py`. 100% reliable.

## Long-derivation details worth preserving
- **Dedup in import_downloads.py uses (dj-first-token, set_date) keys** from existing `dj_sets` rows. This catches the case where my normalizer produced a different filename than a prior manual import (e.g., `mochakk-dc10-ibiza...` vs `mochakk-circoloco-dc10-ibiza...`). Prevents double-ingest.

- **The interview files parse by normalized-token matching.** `dj_slug='cyril-hahn'` normalizes to `cyril hahn` (tokens) and matches `## Cyril Hahn (Swiss/Vancouver-based deep house producer)` because both tokens appear in the heading. Keep this in mind when naming DJs — avoid ambiguity (e.g., don't have both `## Kink` and `## Kink (Boiler Room set)` — they'll both match slug `kink`).

- **Python classification threshold** is `--min-tracks 15` by default in `dj_archetype.py`. Cyril Hahn has 9, so needs `--min-tracks 8` flag. Most DJs with 1 set cleared the threshold because single "deep" sets (50+ tracks classified) are common; only manually-entered sparse sets like Cyril's hit the limit.

- **Per-DJ panel mode** (`next_move.py --per-dj a,b,c`) uses `window=25%` by default (broader than single-DJ mode's 15%) because per-DJ data is sparser.

## Credentials and API state (current)
- **Spotify**: `music-scraper-2` app, client ID `7186b73b1b4a48949e0990e8ed499f0e`. Still under rate-limit observation — last 429 ban Retry-After 38,929s triggered 2026-04-21 ~10 AM, should be clear by now.
- **Last.fm**: key `ed85898815c574d69b0e597cbdde854b` (in `.env`). 5 req/s limit. Safe for all analytical operations.
- **Bright Data**: API key `2b576f4c-...`, customer `hl_02c0928d`, zone `web_unlocker1`, password `98d5odj6xyfn`. Datacenter proxy endpoint `brd.superproxy.io:33335`.
- **ACRCloud**: host `identify-us-west-2.acrcloud.com`, key `29befde9fa80a0c78413e5cac2a7bc20`, secret `3aanO2D1R49AsMiaqCWdQN7IQtvJhgj1VpDOUeCv`. 14-day trial, 3000 request quota. Wired into `identify_youtube_set.py` but not yet `live_listen.py`.

## User context preserved
- User is a former DJ (5 years). Current project goal: turn their Spotify library + observed DJ sets into a practice/planning system.
- Collaborator `maygong` was invited to GitHub repo (2026-04-20). `SETUP.md` exists for her onboarding.
- User prefers terse updates (confirmed multiple times this session and earlier).
- User downloads sets manually in their browser and drops them in `~/Downloads` when automation fails. This is their preferred fallback workflow.
- User's focus is **how to build 2-4 hour sets**. Space Miami is their primary reference venue.

## Things that took a long time to figure out
- The ID-placeholder bug took ~3 queries to diagnose (saw "44 progressive trance for Mochakk" → thought it was tag taxonomy issue → checked individual tracks → found all were "ID - ID" → traced to shared spotify_id → diagnosed the root cause).
- Mochakk's Playwright failures at 7/8 before it became clear we needed manual save. Saved ~30 minutes by catching this pattern early on future DJs.
- The werkzeug/Flask version mismatch wasted ~5 minutes. `pip3 install --user "werkzeug<2.4"` was the fix.
