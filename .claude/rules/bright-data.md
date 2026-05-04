# Bright Data Rules

## Web Unlocker API
- `render: True` returns empty responses for 1001Tracklists (Cloudflare Turnstile)
- Non-rendered requests return HTML shell with no track data (100% JS-loaded)
- API key works fine for simple pages — the issue is 1001TL specifically

## Working Approach: Playwright + BD Proxy
- Use Playwright headless browser with BD proxy at `brd.superproxy.io:33335`
- Proxy auth: `brd-customer-hl_02c0928d-zone-web_unlocker1:98d5odj6xyfn`
- MUST set `ignore_https_errors=True` (BD proxy rewrites SSL certs)
- Tracklist pages: auto-click `input[type=submit]` to bypass CAPTCHA gate
- DJ/search pages: Turnstile CAPTCHA blocks headless — use Google search instead

## Credentials
- API Key: 2b576f4c-c779-4314-a82e-ae7ae110cd2a
- Customer ID: hl_02c0928d
- Zone: web_unlocker1
- Zone Password: 98d5odj6xyfn

## 1001Tracklists Structure
- CAPTCHA gate: form with hidden `captcha=1` + `cf-turnstile-response`
- Track data: `.trackValue` CSS selector → "Artist - Title" text
- Only 0.7% of tracks have embedded Spotify URIs — cannot skip search

## Shell-redirect pattern (added 2026-04-22)
- Turnstile doesn't return 403 — it serves 1001TL's homepage HTML (~60-80KB) when it doesn't let the scraper through
- Generic title: `"1001Tracklists ⋅ The World's Leading DJ Tracklist/Playlist Database"` + 0 `.trackValue` elements
- **Indistinguishable from legit HTML by file size alone.** Must check `.trackValue` count.
- Guard added to `tracklist_scraper.py:fetch_and_save()`: refuses to persist HTML where `'class="trackValue"' not in html` AND title matches the homepage signature
- `sets_broken/` is the quarantine dir for these — don't re-scrape URLs listed there

## Per-URL fingerprinting (added 2026-04-22)
- Turnstile fingerprints specific URLs / proxy IP pairs over time
- Empirical: Duke Dumont URLs cleared 7/10 via Playwright+BD; Mochakk URLs cleared 1/8 (and the 1 was a wrongly-matched Google search result, not a real Mochakk set)
- If >50% of attempts fail on a given DJ's URLs, STOP and switch to manual save — retrying burns time/quota without changing outcomes

## Reliability ranking (added 2026-04-22)
| Path | Status |
|---|---|
| Manual browser save → `import_downloads.py` | 100% reliable |
| `python3 tracklist_scraper.py --artist` (Playwright+BD datacenter) | ~70% bulk, can fail completely per-URL |
| `mcp__Bright_Data__scrape_as_markdown` | Untested successfully; 1 "internal error" observed 2026-04-22, user cancelled subsequent tests |
| `mcp__Bright_Data__scrape_batch` | Untested |
| BD Scraping Browser (not yet configured) | Advertised ~95% for 1001TL, costs ~$5/GB — upgrade path |
| 2Captcha integration (not built) | ~$3/1000 solves — alt upgrade |

## Manual save workflow (added 2026-04-22)
1. User opens URL in Chrome (real browser = trusted fingerprint, bypasses Turnstile)
2. Cmd+S → "Webpage, HTML Only" → save to `~/Downloads` (keep default filename)
3. `python3 import_downloads.py` — handles rename + dedup + ingest + classification

The script normalizes filenames like `"DJ Name @ Venue, Date.html"` → `dj-name-venue-date_html.html` (matches `ingest_sets.py` conventions). Dedup uses (dj-first-token, set-date) from existing `dj_sets` rows so manual-rename variations don't create duplicates.
