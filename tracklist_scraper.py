"""
1001Tracklists scraper using Bright Data to bypass CAPTCHAs.

Fetches DJ set tracklists via Bright Data's web unlocker (handles CloudFlare,
CAPTCHAs, JS rendering), extracts tracks, and optionally creates a Spotify playlist.

Usage:
  # Search by artist name — finds their sets on 1001Tracklists and scrapes them
  python3 tracklist_scraper.py --artist "Hot Since 82"
  python3 tracklist_scraper.py --artist "Hot Since 82" --sets 5 --playlist "HS82 Mix"

  # Multiple artists
  python3 tracklist_scraper.py --artist "Hot Since 82, Honey Dijon, DJ Boring"

  # Scrape direct URLs
  python3 tracklist_scraper.py "https://www.1001tracklists.com/tracklist/..."

  # Scrape and create Spotify playlist
  python3 tracklist_scraper.py --playlist "My Mix" url1 url2

  # Just extract from already-saved HTML files
  python3 tracklist_scraper.py --local sets/
"""

import os
import re
import sys
import csv
import json
import time
import unicodedata
import logging
import argparse
from urllib.parse import quote_plus
import requests
from spotipy.exceptions import SpotifyException

import spotify_guard  # noqa: F401 — charges every request to the daily budget
from bs4 import BeautifulSoup, NavigableString
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

BRIGHT_DATA_API_KEY = os.getenv("BRIGHT_DATA_API_KEY")
BRIGHTDATA_ZONE = os.getenv("BRIGHTDATA_ZONE", "web_unlocker1")
BRIGHTDATA_CUSTOMER_ID = os.getenv("BRIGHTDATA_CUSTOMER_ID")
BRIGHTDATA_ZONE_PASSWORD = os.getenv("BRIGHTDATA_ZONE_PASSWORD")
BRIGHTDATA_API_URL = "https://api.brightdata.com/request"

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

# Shared Playwright browser instance (reused across fetches)
_pw = None
_browser = None


def _get_browser():
    """Get or create a shared Playwright browser with Bright Data proxy."""
    global _pw, _browser
    if _browser and _browser.is_connected():
        return _browser

    if not BRIGHTDATA_CUSTOMER_ID or not BRIGHTDATA_ZONE_PASSWORD:
        log.error("BRIGHTDATA_CUSTOMER_ID and BRIGHTDATA_ZONE_PASSWORD must be set in .env")
        return None

    proxy_user = f"brd-customer-{BRIGHTDATA_CUSTOMER_ID}-zone-{BRIGHTDATA_ZONE}"
    _pw = sync_playwright().start()
    _browser = _pw.chromium.launch(
        headless=True,
        proxy={
            "server": "http://brd.superproxy.io:33335",
            "username": proxy_user,
            "password": BRIGHTDATA_ZONE_PASSWORD,
        },
    )
    return _browser


def _close_browser():
    """Clean up Playwright resources."""
    global _pw, _browser
    if _browser:
        _browser.close()
        _browser = None
    if _pw:
        _pw.stop()
        _pw = None


# ---------------------------------------------------------------------------
# 1. FETCH — Playwright + Bright Data proxy with CAPTCHA auto-submit
# ---------------------------------------------------------------------------

def fetch_html(url, retries=4, delay=8):
    """Fetch a 1001Tracklists page via Playwright + Bright Data proxy.

    The CAPTCHA bypass is flaky — Cloudflare Turnstile sometimes needs multiple
    submit clicks and longer waits before yielding the real page. Retry up to 4
    times, verify the captcha input is gone after submit.
    """
    browser = _get_browser()
    if not browser:
        return None

    for attempt in range(1, retries + 1):
        log.info(f"[{attempt}/{retries}] Fetching {url}")
        context = None
        try:
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)

            # Auto-submit CAPTCHA gate if present. Sometimes needs multiple clicks
            # because Cloudflare Turnstile may issue a fresh challenge.
            captcha_was_present = False
            for click_attempt in range(3):
                submit_btn = page.query_selector("input[type=submit]")
                if not submit_btn:
                    break
                captcha_was_present = True
                log.info(f"  Submitting CAPTCHA gate (click {click_attempt + 1})...")
                try:
                    submit_btn.click()
                    page.wait_for_load_state("domcontentloaded", timeout=60000)
                except Exception as e:
                    log.warning(f"  Submit click failed: {e}")
                # Turnstile needs ≥10s for token validation; some pages take longer.
                page.wait_for_timeout(10000)

            # If CAPTCHA was present, the gate often redirects us to the home/search
            # page after validation rather than the requested URL. Re-navigate.
            if captcha_was_present:
                current_url = page.url
                if current_url.rstrip("/") != url.rstrip("/"):
                    log.info(f"  Re-navigating to {url} after CAPTCHA (was at {current_url[:60]}...)")
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=60000)
                        page.wait_for_timeout(3000)
                    except Exception as e:
                        log.warning(f"  Re-navigate failed: {e}")

            # Newer 1001TL pages render the tracklist via JS after page load.
            # Wait for .trackValue (the main track-row selector) to appear in DOM.
            try:
                page.wait_for_selector(".trackValue", timeout=20000)
                log.info("  .trackValue rendered — tracklist loaded")
            except Exception:
                log.info("  .trackValue not found after 20s — page may genuinely have no tracklist")

            html = page.content()

            # Sanity check — did we get actual content?
            if not html or len(html) < 1000:
                log.warning("  Got empty or very short page, retrying...")
                time.sleep(delay)
                continue

            if "Error 403" in html[:2000] or "cf-challenge" in html[:5000]:
                log.warning("  Got a CAPTCHA/challenge page, retrying...")
                time.sleep(delay)
                continue

            # Final check — is the captcha input still present? If so, the gate
            # never lifted. Retry with a fresh context.
            if 'name="captcha"' in html or "name='captcha'" in html:
                log.warning("  CAPTCHA gate still present after submit — retrying with fresh context...")
                time.sleep(delay)
                continue

            return html

        except Exception as e:
            log.warning(f"  Fetch failed: {e}")
        finally:
            if context:
                context.close()

        if attempt < retries:
            time.sleep(delay)

    log.error(f"Failed to fetch {url} after {retries} attempts")
    return None


def fetch_and_save(url, output_dir="sets"):
    """Fetch a URL and save the HTML to disk. Refuses to save hollow shell redirects."""
    html = fetch_html(url)
    if not html:
        return None, None

    # Shell-page guard: if Turnstile redirected us to the 1001TL homepage instead of
    # the real tracklist, the HTML will have the generic title and no .trackValue
    # elements. Don't persist that — it pollutes sets/ and downstream ingest.
    if 'class="trackValue"' not in html and "1001Tracklists ⋅ The World's Leading" in html[:2000]:
        log.warning(f"  Got shell-redirect HTML (no .trackValue, generic title) for {url} — not saving")
        return None, None

    os.makedirs(output_dir, exist_ok=True)
    # Create a filename from the URL slug
    slug = url.rstrip("/").split("/")[-1] or "tracklist"
    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", slug)[:80]
    filename = f"{slug}.html"
    filepath = os.path.join(output_dir, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)
    log.info(f"Saved HTML to {filepath}")
    return html, filepath


BASE_URL = "https://www.1001tracklists.com"


# ---------------------------------------------------------------------------
# 2. ARTIST SEARCH — find DJ page + their sets on 1001Tracklists
# ---------------------------------------------------------------------------

def _artist_slug(artist_name):
    """Turn 'Ferra Black' into 'ferra-black' for URL matching."""
    return re.sub(r"[^a-z0-9]+", "-", artist_name.lower()).strip("-")


def _search_google_for_tracklists(artist_name, max_results=10):
    """
    Search Google for 1001Tracklists URLs for an artist.
    Uses the shared Playwright browser with Bright Data proxy.
    Paginates Google results to find up to max_results URLs.
    Returns list of tracklist URLs.
    """
    browser = _get_browser()
    if not browser:
        return []

    slug = _artist_slug(artist_name)
    query = f"site:1001tracklists.com/tracklist {artist_name}"
    seen = set()
    unique = []
    context = None
    try:
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()

        # Paginate through Google results (10 per page)
        for start in range(0, max_results + 10, 10):
            page.goto(
                f"https://www.google.com/search?q={quote_plus(query)}&num=10&start={start}",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(3000)

            html = page.content()
            urls = re.findall(
                r'https?://(?:www\.)?1001tracklists\.com/tracklist/[a-z0-9]+/[a-z0-9_-]+\.html',
                html,
            )

            new_count = 0
            for url in urls:
                url = url.replace("http://", "https://")
                if "www." not in url:
                    url = url.replace("1001tracklists.com", "www.1001tracklists.com")
                if url not in seen:
                    seen.add(url)
                    unique.append(url)
                    new_count += 1

            log.info(f"  Google page {start // 10 + 1}: {new_count} new URLs")
            if new_count == 0 or len(unique) >= max_results:
                break

        # Prioritize artist's own sets (URL slug starts with artist slug)
        own = [u for u in unique if u.split("/")[-1].replace(".html", "").startswith(slug)]
        other = [u for u in unique if u not in own]
        prioritized = own + other

        return prioritized[:max_results]
    except Exception as e:
        log.warning(f"  Google search failed: {e}")
        return unique[:max_results]
    finally:
        if context:
            context.close()


def search_and_get_sets(artist_name, max_sets=10):
    """
    Find tracklist URLs for an artist via Google search.
    Returns list of tracklist URLs to scrape.
    """
    log.info(f"\nSearching for '{artist_name}' tracklists via Google...")
    urls = _search_google_for_tracklists(artist_name, max_results=max_sets)
    if urls:
        slug = _artist_slug(artist_name)
        own = [u for u in urls if slug in u.split("/")[-1]]
        log.info(f"  Found {len(urls)} tracklists ({len(own)} own sets)")
    else:
        log.error(f"  Could not find '{artist_name}' on 1001Tracklists")
    return urls


def get_tracklist_urls_from_html(html, artist_name, max_sets=10):
    """
    Parse a DJ profile page HTML and extract tracklist URLs.
    Filters to only include the artist's OWN sets (by matching URL slug).
    """
    slug = _artist_slug(artist_name) if artist_name else ""
    seen = set()
    own_sets = []
    other_sets = []

    # Method 1: regex on raw HTML for /tracklist/ links
    for match in re.findall(r'/tracklist/[a-z0-9]+/[a-z0-9_-]+\.html', html):
        full = BASE_URL + match
        if full not in seen:
            seen.add(full)

    # Method 2: parse <a> tags
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/tracklist/" in href and href.endswith(".html"):
            full = href if href.startswith("http") else BASE_URL + href
            if full not in seen:
                seen.add(full)

    # Sort into own sets vs featuring sets
    for full in seen:
        url_slug = full.split("/")[-1].replace(".html", "")
        if slug and url_slug.startswith(slug):
            own_sets.append(full)
        else:
            other_sets.append(full)

    if own_sets:
        log.info(f"  {len(own_sets)} own sets, {len(other_sets)} featuring sets (skipped)")
        return own_sets[:max_sets]
    return (own_sets + other_sets)[:max_sets]


# ---------------------------------------------------------------------------
# 3. EXTRACT — multiple strategies to pull tracks from HTML
# ---------------------------------------------------------------------------

def extract_tracks(html, source=""):
    """
    Extract (artist, title) tuples from 1001Tracklists HTML.
    Tries multiple strategies in order of reliability.
    """
    soup = BeautifulSoup(html, "html.parser")
    tracks = []

    # Gate check: is this a CAPTCHA/validation page instead of the tracklist?
    # 1001TL's gate HTML is ~60K of shell + "We need to validate your are real human!"
    # with <input name="captcha"> and no track markup. Strategies would all return 0
    # and produce a confusing "No tracks extracted" log — flag it explicitly instead.
    if _is_captcha_gate(soup, html):
        log.warning(
            f"  CAPTCHA gate detected in {source or 'HTML'} — fetch layer did not bypass "
            f"the 'validate you are real human' page. Re-fetch this URL."
        )
        return []

    # Strategy 1: JSON-LD structured data (most reliable when present)
    tracks = _extract_jsonld(soup, source)
    if tracks:
        log.info(f"  Extracted {len(tracks)} tracks via JSON-LD")
        return tracks

    # Strategy 2: Spotify URIs embedded in the page
    tracks = _extract_spotify_uris(soup, source)
    if tracks:
        log.info(f"  Extracted {len(tracks)} tracks via Spotify URIs")
        return tracks

    # Strategy 3: .trackValue CSS selector (1001Tracklists main format)
    tracks = _extract_track_value(soup, source)
    if tracks:
        log.info(f"  Extracted {len(tracks)} tracks via .trackValue")
        return tracks

    # Strategy 4: .trackFormat__text CSS selector (alternate format)
    tracks = _extract_track_format(soup, source)
    if tracks:
        log.info(f"  Extracted {len(tracks)} tracks via .trackFormat__text")
        return tracks

    # Strategy 5: Schema.org MusicRecording metadata
    tracks = _extract_schema_org(soup, source)
    if tracks:
        log.info(f"  Extracted {len(tracks)} tracks via Schema.org")
        return tracks

    # Strategy 5.5: itemprop=tracks microdata (broader than Strategy 5 — matches any
    # tag and both http://schema.org/MusicRecording and https://schema.org/MusicRecording).
    tracks = _extract_itemprop_tracks(soup, source)
    if tracks:
        log.info(f"  Extracted {len(tracks)} tracks via itemprop=tracks microdata")
        return tracks

    # Strategy 6: Regex on page text ("1. Artist - Title" pattern)
    tracks = _extract_numbered_list(soup, source)
    if tracks:
        log.info(f"  Extracted {len(tracks)} tracks via numbered list")
        return tracks

    # Strategy 7: Broad text fallback
    tracks = _extract_text_fallback(soup, source)
    if tracks:
        log.info(f"  Extracted {len(tracks)} tracks via text fallback")
        return tracks

    log.warning(f"  No tracks extracted from {source or 'HTML'}")
    return []


def _is_captcha_gate(soup, html):
    """Return True if the HTML is a 1001TL CAPTCHA/validation gate rather than a tracklist."""
    # Small HTML (< ~80KB) with a captcha input and no track markup is a dead giveaway.
    if soup.select_one("input[name='captcha']") is None:
        return False
    if soup.select(".trackValue") or soup.select(".trackFormat__text"):
        return False
    if soup.select("[itemtype*='MusicRecording']"):
        return False
    # Textual signature from the gate page.
    body = soup.find("body")
    body_text = body.get_text(" ", strip=True) if body else ""
    if "validate" in body_text.lower() and "human" in body_text.lower():
        return True
    # Fallback: tiny page with captcha input and nothing else is still a gate.
    return len(html) < 100000


def _extract_itemprop_tracks(soup, source):
    """
    Broader microdata selector than _extract_schema_org.
    Matches any element with itemprop='tracks' and pulls name/byArtist meta children.
    """
    tracks = []
    for el in soup.select("[itemprop='tracks']"):
        name_meta = el.find("meta", {"itemprop": "name"})
        artist_meta = el.find("meta", {"itemprop": "byArtist"})
        if not name_meta:
            continue
        name = (name_meta.get("content") or "").strip()
        artist = (artist_meta.get("content") or "").strip() if artist_meta else ""
        if not name:
            continue
        # name often has "Artist - Title" form; prefer byArtist + split title.
        title = name
        if artist and name.startswith(artist + " - "):
            title = name[len(artist) + 3:].strip()
        elif " - " in name and not artist:
            artist, title = name.split(" - ", 1)
            artist, title = artist.strip(), title.strip()
        if artist and title:
            tracks.append((artist, title, source))
    return tracks


def _extract_jsonld(soup, source):
    tracks = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") == "MusicRecording":
                    artist = item.get("byArtist", {})
                    artist_name = artist.get("name", "") if isinstance(artist, dict) else str(artist)
                    title = item.get("name", "")
                    if artist_name and title:
                        tracks.append((artist_name.strip(), title.strip(), source))
        except (json.JSONDecodeError, TypeError):
            continue
    return tracks


def _extract_spotify_uris(soup, source):
    """Extract Spotify track URIs directly embedded in the page."""
    tracks = []
    for el in soup.find_all(attrs={"data-spotify": True}):
        uri = el.get("data-spotify", "")
        if uri.startswith("spotify:track:"):
            # We store the URI as the title — Spotify lookup will handle it
            tracks.append(("spotify_uri", uri, source))

    # Also check for Spotify embed links
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "open.spotify.com/track/" in href:
            track_id = href.split("/track/")[-1].split("?")[0]
            tracks.append(("spotify_uri", f"spotify:track:{track_id}", source))

    return tracks


def _extract_track_value(soup, source):
    tracks = []
    for el in soup.select(".trackValue"):
        text = el.get_text(strip=True)
        # 1001TL uses "Artist-Title" (no spaces) or "Artist - Title"
        if " - " in text:
            artist, title = text.split(" - ", 1)
        elif "-" in text:
            artist, title = text.split("-", 1)
        else:
            continue
        artist, title = artist.strip(), title.strip()
        if artist and title:
            tracks.append((artist, title, source))
    return tracks


def _extract_track_format(soup, source):
    tracks = []
    for row in soup.select(".trackFormat__text"):
        text = row.get_text(strip=True)
        if " - " in text:
            artist, title = text.split(" - ", 1)
            tracks.append((artist.strip(), title.strip(), source))
    return tracks


def _extract_schema_org(soup, source):
    tracks = []
    for div in soup.select("div[itemtype='http://schema.org/MusicRecording']"):
        title_el = div.find("meta", {"itemprop": "name"})
        artist_el = div.find("meta", {"itemprop": "byArtist"})
        if title_el and artist_el:
            title = title_el.get("content", "").strip()
            artist = artist_el.get("content", "").strip()
            if title and artist:
                tracks.append((artist, title, source))
    return tracks


def _extract_numbered_list(soup, source):
    """Parse "1. Artist - Title" or "1. Artist – Title" patterns."""
    tracks = []
    text = soup.get_text()
    # Match numbered entries with dash or en-dash
    for m in re.finditer(r'\d+\.\s+(.+?)\s+[-–]\s+(.+)', text):
        artist, title = m.group(1).strip(), m.group(2).strip()
        if 1 < len(artist) < 100 and 1 < len(title) < 200:
            tracks.append((artist, title, source))
    return tracks


def _extract_text_fallback(soup, source):
    """Last resort — find lines with 'Artist - Title' pattern."""
    tracks = []
    text = soup.get_text("\n", strip=True)
    for line in text.splitlines():
        line = line.strip()
        if " - " in line and len(line.split(" - ")) == 2:
            artist, title = line.split(" - ", 1)
            artist, title = artist.strip(), title.strip()
            if 2 < len(title) < 150 and 1 < len(artist) < 100:
                tracks.append((artist, title, source))
    return tracks


def clean_track_name(artist, title):
    """Remove redundant artist prefix from title."""
    pattern = re.compile(rf"^{re.escape(artist)}\s*[-:|]\s*", re.IGNORECASE)
    return re.sub(pattern, "", title).strip()


# ---------------------------------------------------------------------------
# 3. SPOTIFY — search + playlist creation
# ---------------------------------------------------------------------------

def get_spotify():
    """Lazy-init Spotify client. Uses a plain session to prevent spotipy from retrying 429s."""
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth

    # Pass a pre-built session so spotipy doesn't install its retry adapter
    session = requests.Session()
    return spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            scope="playlist-modify-public playlist-modify-private playlist-read-private playlist-read-collaborative",
            client_id=os.getenv("SPOTIPY_CLIENT_ID"),
            client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
            redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
        ),
        requests_session=session,
    )


BATCH_SIZE = 50          # search this many tracks, then add to playlist
BATCH_PAUSE = 120        # seconds to pause between batches (doubled to avoid sustained-rate 429)
TRACK_DELAY = 4          # seconds between each search call (~8 calls per 30s window)


class SpotifyRateLimited(Exception):
    pass


class _SpotifyIdCache(dict):
    """Artist/title -> Spotify ID, backed by the `spotify_id_cache` table.

    Behaves like the plain dict the JSON file used to produce, so every
    existing call site keeps working, but writes land in library.db. Assigning
    a key persists it immediately, which is what makes a mid-run rate-limit
    ban non-destructive.

    Known misses (searched, no match) are held in `.misses` and stored as rows
    with a NULL spotify_id, so a re-run never pays for the same dead end.
    """

    def __init__(self, rows):
        super().__init__((k, v) for k, v in rows if v is not None)
        self.misses = {k for k, v in rows if v is None}

    def _persist(self, key, spotify_id):
        from db import connect
        with connect() as conn:
            conn.execute(
                "INSERT INTO spotify_id_cache (cache_key, spotify_id, searched_at) "
                "VALUES (?,?,CURRENT_TIMESTAMP) "
                "ON CONFLICT(cache_key) DO UPDATE SET "
                "  spotify_id=excluded.spotify_id, searched_at=excluded.searched_at",
                (key, spotify_id))
            conn.commit()

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self._persist(key, value)

    def mark_miss(self, key):
        """Record that this key was searched and Spotify returned nothing."""
        self.misses.add(key)
        self._persist(key, None)


def _load_spotify_cache():
    """Load the ID cache from library.db (was spotify_cache.json)."""
    from db import connect
    with connect() as conn:
        rows = conn.execute(
            "SELECT cache_key, spotify_id FROM spotify_id_cache").fetchall()
    return _SpotifyIdCache([(r[0], r[1]) for r in rows])


def _save_spotify_cache(cache):
    """No-op: writes are persisted per-key on assignment.

    Kept so existing callers do not need editing, and so a crash between
    "found the ID" and "finished the batch" can no longer lose it.
    """
    return


def _cache_key(artist, title):
    return f"{artist.strip().lower()}||{title.strip().lower()}"


_MATCH_STOP = {"the", "a", "an", "and", "feat", "ft", "featuring", "with", "vs",
               "versus", "presents", "pres", "dj", "mr", "x", "remix", "mix", "edit"}


def _match_tokens(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return {w for w in s.split() if w and w not in _MATCH_STOP and len(w) > 1}


def _is_same_track(artist, title, item):
    """Is this search hit actually the track we asked for?

    Spotify ranks *something* first for every query. Taking `items[0]` blind
    is how a High Contrast jungle mix became Kid Cudi's "Maui Wowie" — and the
    cache then served that as truth to every later run. 158 entries were wrong
    this way; see repair_cache_mismatches.py.

    Artist is the reliable signal. Titles legitimately differ by remix suffix
    ("Stranger(DJ BORINGremix)" vs "Stranger - DJ BORING Extended Mix"), so a
    strong title agreement is accepted as a fallback for tracklists that credit
    the remixer in the artist field.
    """
    want_a = _match_tokens(artist)
    got_a = set()
    for a in item.get("artists", []):
        got_a |= _match_tokens(a.get("name"))
    if want_a & got_a:
        return True
    want_t, got_t = _match_tokens(title), _match_tokens(item.get("name"))
    shared = want_t & got_t
    return bool(shared) and len(shared) >= max(1, min(len(want_t), len(got_t)) // 2)


def find_spotify_track(sp, artist, title, cache):
    """Search Spotify for a single track with caching.
    Returns track ID or None. Raises SpotifyRateLimited on 429."""
    if artist == "spotify_uri":
        return title.replace("spotify:track:", "")

    key = _cache_key(artist, title)
    if key in cache:
        return cache[key]  # cached ID or None (not found)

    queries = [
        f"track:{title} artist:{artist}",
        f"{artist} {title}",
    ]
    for q in queries:
        try:
            results = sp.search(q=q, type="track", limit=5)
            items = results.get("tracks", {}).get("items", []) or []
            # Prefer an exact title match before any fuzzy acceptance, so a
            # request for "Hell suite, Pt. I" cannot settle for "Pt. II" just
            # because the artist agrees.
            want_t = _match_tokens(title)
            ranked = sorted(
                (i for i in items if _is_same_track(artist, title, i)),
                key=lambda i: 0 if _match_tokens(i.get("name")) == want_t else 1)
            if ranked:
                tid = ranked[0]["id"]
                cache[key] = tid
                return tid
        except Exception as e:
            if "429" in str(e) or "rate" in str(e).lower() or "Too many" in str(e):
                raise SpotifyRateLimited(str(e))
            continue

    cache[key] = None  # not found — don't search again
    return None


# name.lower() -> (playlist_id, url). Built once per process; a full scan of a
# 1500-playlist account is ~31 requests, and rescanning per lookup is what
# tripped Spotify's QUOTA_EXCEEDED during bulk builds.
_PLAYLIST_INDEX = None


# A short 429 is worth waiting out. A long Retry-After means the app is in an
# extended quota cooldown (Spotify hands out multi-hour bans to dev-mode apps
# that burn through the window) — sleeping through that would hang the process
# for the better part of a day, so bail and let the caller resume later.
MAX_RETRY_AFTER = 120


def _spotify_call(fn, *args, **kwargs):
    """Call a spotipy method under the daily budget, honouring Retry-After.

    Budget accounting is NOT done here — auth.py patches spotipy's
    `_internal_call`, which is the true HTTP chokepoint and catches the ~90
    direct `sp.foo()` calls elsewhere in the repo that never reach this
    function. Charging in both places would double-count.
    """
    from spotify_budget import note_429

    for attempt in range(6):
        try:
            return fn(*args, **kwargs)
        except SpotifyException as e:
            if e.http_status != 429 or attempt == 5:
                raise
            wait = int((e.headers or {}).get("Retry-After", 5)) + 1
            if wait > MAX_RETRY_AFTER:
                until = note_429(wait)
                hours = wait / 3600
                raise RuntimeError(
                    f"Spotify quota exceeded — Retry-After is {wait}s (~{hours:.1f}h). "
                    f"Recorded a cooldown until {until}; later runs refuse to call "
                    f"rather than spend a request rediscovering this. "
                    f"Re-run after that time — nothing is lost."
                ) from e
            log.warning(f"  429 from Spotify — sleeping {wait}s (attempt {attempt + 1}/5)")
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _index_from_db():
    """Playlist name -> (id, url) straight from the `playlists` table.

    sync_playlists.py keeps that table current, so the 17 API pages a live
    scan costs are pure waste on every build. None if unpopulated.
    """
    try:
        from db import connect
        with connect() as conn:
            rows = conn.execute("SELECT playlist_id, name FROM playlists").fetchall()
    except Exception:
        return None
    if not rows:
        return None
    return {r["name"].strip().lower():
            (r["playlist_id"],
             f"https://open.spotify.com/playlist/{r['playlist_id']}")
            for r in rows if r["name"]}


def _build_playlist_index(sp):
    """Page through every playlist the user owns, once."""
    index = {}
    offset = 0
    while True:
        page = _spotify_call(sp.current_user_playlists, limit=50, offset=offset)
        items = page.get("items") or []
        for pl in items:
            if pl and pl.get("name"):
                key = pl["name"].strip().lower()
                # First occurrence wins — matches the old scan order.
                index.setdefault(key, (pl["id"], pl["external_urls"]["spotify"]))
        # Paginate on `next`, not page length — Spotify emits 49-item pages
        # mid-listing, and stopping early truncates the index, which makes an
        # existing playlist look absent and gets a duplicate created.
        if not page.get("next") or not items:
            break
        offset += 50
        if offset > 5000:
            log.warning("playlist paging hit the safety cap at 5000")
            break
    log.info(f"Indexed {len(index)} playlists")
    return index


def reset_playlist_index():
    """Drop the cached index (call if playlists were renamed out of band)."""
    global _PLAYLIST_INDEX
    _PLAYLIST_INDEX = None


def _get_or_create_playlist(sp, playlist_name):
    """Find or create a playlist. Returns (playlist_id, playlist_url) or (None, None)."""
    global _PLAYLIST_INDEX
    if _PLAYLIST_INDEX is None:
        # Prefer the DB mirror (0 requests); fall back to scanning the account.
        _PLAYLIST_INDEX = _index_from_db()
        if _PLAYLIST_INDEX is None:
            _PLAYLIST_INDEX = _build_playlist_index(sp)
        else:
            log.info(f"Playlist index from library.db "
                     f"({len(_PLAYLIST_INDEX)} playlists, 0 API requests)")

    key = playlist_name.strip().lower()
    hit = _PLAYLIST_INDEX.get(key)
    if hit:
        return hit

    # A miss against the DB mirror is not proof the playlist is absent — it may
    # just be stale. Confirm live once before creating a duplicate.
    if "__live__" not in _PLAYLIST_INDEX:
        _PLAYLIST_INDEX = _build_playlist_index(sp)
        _PLAYLIST_INDEX["__live__"] = ("", "")
        hit = _PLAYLIST_INDEX.get(key)
        if hit:
            return hit

    # Create via /me/playlists (works in dev mode)
    try:
        token = sp.auth_manager.get_access_token(as_dict=False)
        resp = requests.post(
            "https://api.spotify.com/v1/me/playlists",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"name": playlist_name, "public": True},
        )
        if resp.status_code == 201:
            new_pl = resp.json()
            log.info(f"Created playlist: {playlist_name}")
            created = (new_pl["id"], new_pl["external_urls"]["spotify"])
            _PLAYLIST_INDEX[playlist_name.strip().lower()] = created
            # playlist_tracks has a FK to playlists, so membership for a
            # brand-new playlist cannot be recorded until this row exists.
            # Without it _record_membership raised IntegrityError into a bare
            # except and the playlist silently kept zero membership rows —
            # 14 of 87 study playlists were in that state.
            _register_playlist(new_pl["id"], playlist_name)
            return created
    except Exception as e:
        log.error(f"Failed to create playlist: {e}")
    return None, None


def _register_playlist(playlist_id, name):
    """Insert the `playlists` row for a playlist we just created."""
    try:
        from db import connect
        with connect() as conn:
            conn.execute(
                "INSERT INTO playlists (playlist_id, name, is_managed, track_count) "
                "VALUES (?,?,1,0) ON CONFLICT(playlist_id) DO UPDATE SET name=excluded.name",
                (playlist_id, name))
            conn.commit()
    except Exception as e:
        log.warning(f"could not register playlist {name}: {e}")


def _item_track(entry):
    """Pull the track object out of a playlist-items entry.

    Spotify now nests it under "item"; older responses used "track". Accept
    either so a shape change can never silently empty the dedupe set.
    """
    if not entry:
        return None
    return entry.get("item") or entry.get("track")


def playlist_existing_ids(sp, playlist_id):
    """Return the set of track IDs already on a playlist.

    Requests both `item` and `track` sub-objects so it survives either
    response shape. A previous version asked for `items.track.id` only, which
    Spotify answers with empty objects — the dedupe set came back empty and
    every rebuild re-added the whole playlist.
    """
    existing = set()
    offset = 0
    while True:
        resp = _spotify_call(
            sp.playlist_tracks,
            playlist_id,
            fields="items(item(id),track(id)),next",
            limit=100,
            offset=offset,
        )
        items = resp.get("items") or []
        if not items:
            break
        for entry in items:
            track = _item_track(entry)
            if track and track.get("id"):
                existing.add(track["id"])
        offset += 100
        # Paginate on `next`. Spotify emits short pages mid-listing, so a
        # length check truncates the read — and a truncated dedupe set
        # means duplicates get added.
        if not resp.get("next"):
            break
    return existing


def _remove_tracks_from_playlist(sp, playlist_id, track_ids):
    """Remove specific track IDs from a playlist (100 at a time).

    Updates `playlist_tracks` too. Only `_sync_playlist_tracks` used to record
    membership, so calling this helper directly left the DB claiming tracks the
    playlist no longer held — and `cached_membership` would then hand that stale
    set to the next run as if it were fresh.
    """
    ids = [t for t in track_ids if t]
    for i in range(0, len(ids), 100):
        _spotify_call(sp.playlist_remove_all_occurrences_of_items,
                      playlist_id, ids[i:i + 100])
    _forget_membership(playlist_id, ids)
    return len(ids)


def _forget_membership(playlist_id, removed_ids):
    """Drop removed tracks from the stored membership for one playlist."""
    if not removed_ids:
        return
    try:
        from db import connect
        with connect() as conn:
            conn.executemany(
                "DELETE FROM playlist_tracks WHERE playlist_id=? AND spotify_id=?",
                [(playlist_id, i) for i in removed_ids])
            conn.execute(
                "UPDATE playlists SET track_count = "
                "(SELECT COUNT(*) FROM playlist_tracks WHERE playlist_id=?) "
                "WHERE playlist_id=?", (playlist_id, playlist_id))
            conn.commit()
        _forget_from_mongo(playlist_id, removed_ids)
    except Exception:
        pass  # accounting must never break the work it accounts for


def cached_membership(playlist_id):
    """Membership from library.db, or None if we cannot trust it.

    Trustworthy only when `playlists.snapshot_id` was refreshed this run
    (sync_playlists.sync_list) — Spotify changes that value iff the contents
    changed, so a match means re-reading would return exactly these rows.
    """
    try:
        from db import connect
        with connect() as conn:
            fresh = conn.execute(
                "SELECT 1 FROM playlists WHERE playlist_id=? AND snapshot_id "
                "IS NOT NULL", (playlist_id,)).fetchone()
            if not fresh:
                return None
            rows = conn.execute(
                "SELECT spotify_id FROM playlist_tracks WHERE playlist_id=?",
                (playlist_id,)).fetchall()
    except Exception:
        return None
    return {r[0] for r in rows} if rows else None


def _mirror_to_mongo(playlist_id, playlist_name, ids):
    """Mirror membership into the MongoDB catalogue, keyed by subgenre.

    Hooked here because this is the one function every membership change passes
    through. Policing call sites was tried in this repo for the Spotify budget
    and missed ~90 of them.

    Never raises and never blocks the write it mirrors: library.db stays the
    source of truth, and `mongo_catalog.sync` repairs any drift.
    """
    if not playlist_name:
        return
    try:
        import mongo_catalog
        mongo_catalog.record_playlist(playlist_id, playlist_name, ids)
    except Exception as e:
        log.warning(f"mongo mirror skipped for {playlist_name}: {e}")


def _forget_from_mongo(playlist_id, removed_ids):
    """Drop a playlist from the removed tracks' membership lists."""
    if not removed_ids:
        return
    try:
        import mongo_catalog
        mongo_catalog.tracks().update_many(
            {"_id": {"$in": list(removed_ids)}},
            {"$pull": {"playlists": {"playlist_id": playlist_id}}})
    except Exception as e:
        log.warning(f"mongo un-mirror skipped: {e}")


def _record_membership(playlist_id, ids):
    """Keep playlist_tracks in step with a write we just made."""
    try:
        from db import connect
        with connect() as conn:
            conn.execute("DELETE FROM playlist_tracks WHERE playlist_id=?",
                         (playlist_id,))
            conn.executemany(
                "INSERT OR IGNORE INTO playlist_tracks (playlist_id, spotify_id) "
                "VALUES (?,?)", [(playlist_id, i) for i in ids])
            conn.execute("UPDATE playlists SET track_count=? WHERE playlist_id=?",
                         (len(ids), playlist_id))
            name = conn.execute("SELECT name FROM playlists WHERE playlist_id=?",
                                (playlist_id,)).fetchone()
            conn.commit()
        _mirror_to_mongo(playlist_id, name[0] if name else None, ids)
    except Exception as e:
        # Non-fatal by design — accounting must not break the work it accounts
        # for — but it must not be invisible either. A swallowed FK violation
        # here is what left 14 playlists with no membership recorded.
        log.warning(f"could not record membership for {playlist_id}: {e}")


def _sync_playlist_tracks(sp, playlist_id, track_ids, existing=None):
    """Make the playlist contain exactly `track_ids`.

    Returns (added, removed). Only the difference is written, so a playlist
    that is already correct costs zero write requests.
    """
    wanted = [t for t in dict.fromkeys(t for t in track_ids if t)]
    wanted_set = set(wanted)
    if existing is None:
        existing = playlist_existing_ids(sp, playlist_id)

    stale = existing - wanted_set
    if stale:
        _remove_tracks_from_playlist(sp, playlist_id, sorted(stale))

    missing = [t for t in wanted if t not in existing]
    for i in range(0, len(missing), 100):
        _spotify_call(sp.playlist_add_items, playlist_id, missing[i:i + 100])

    if stale or missing:
        _record_membership(playlist_id, wanted)
    return len(missing), len(stale)


def _add_tracks_to_playlist(sp, playlist_id, track_ids):
    """Add tracks to playlist, skipping duplicates."""
    existing = playlist_existing_ids(sp, playlist_id)

    new_ids = [tid for tid in track_ids if tid and tid not in existing]
    if new_ids:
        for i in range(0, len(new_ids), 100):
            _spotify_call(sp.playlist_add_items, playlist_id, new_ids[i:i + 100])
        log.info(f"  Added {len(new_ids)} tracks to playlist")
        _record_membership(playlist_id, sorted(existing | set(new_ids)))
    return len(new_ids)


# ---------------------------------------------------------------------------
# 4. SAVE — CSV output
# ---------------------------------------------------------------------------

def save_tracks_csv(tracks, output_path="tracklist_tracks.csv"):
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["artist", "title", "source"])
        for artist, title, source in tracks:
            if artist != "spotify_uri":
                writer.writerow([artist, clean_track_name(artist, title), source])
    log.info(f"Saved {len(tracks)} tracks to {output_path}")


# ---------------------------------------------------------------------------
# 5. MAIN
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="1001Tracklists scraper with Bright Data CAPTCHA bypass")
    parser.add_argument("urls", nargs="*", help="1001Tracklists URLs to scrape")
    parser.add_argument("--artist", help="Search by artist name (comma-separated for multiple)")
    parser.add_argument("--dj-url", help="Direct DJ page URL(s), comma-separated (e.g. https://www.1001tracklists.com/dj/enzosiragusa/index.html)")
    parser.add_argument("--dj-local", help="Local DJ page HTML file (extract set URLs then fetch each set)")
    parser.add_argument("--sets", type=int, default=5, help="Max sets to scrape per artist (default: 5)")
    parser.add_argument("--local", help="Path to local HTML file or directory (skip fetching)")
    parser.add_argument("--playlist", help="Create a Spotify playlist with this name")
    parser.add_argument("--output", default="tracklist_tracks.csv", help="Output CSV path")
    parser.add_argument("--save-html", default="sets", help="Directory to save fetched HTML (default: sets/)")
    args = parser.parse_args()

    if not args.urls and not args.local and not args.artist and not args.dj_url and not args.dj_local:
        parser.print_help()
        sys.exit(1)

    all_tracks = []

    # --- DJ local mode: read local DJ page HTML, extract set URLs, fetch & scrape them ---
    if args.dj_local:
        with open(args.dj_local, "r", encoding="utf-8") as f:
            dj_html = f.read()
        slug = os.path.basename(args.dj_local).replace(".html", "").replace("_files", "")
        log.info(f"Using local DJ page: {args.dj_local} (slug: {slug})")
        tracklist_urls = get_tracklist_urls_from_html(dj_html, slug, max_sets=args.sets)
        if tracklist_urls:
            for i, url in enumerate(tracklist_urls, 1):
                log.info(f"\n[{slug}] Scraping set {i}/{len(tracklist_urls)}: {url}")
                html, filepath = fetch_and_save(url, output_dir=args.save_html)
                if html:
                    source = os.path.basename(filepath) if filepath else url
                    tracks = extract_tracks(html, source=source)
                    all_tracks.extend(tracks)
                    log.info(f"  Got {len(tracks)} tracks")
                if i < len(tracklist_urls):
                    time.sleep(2)
        else:
            log.warning(f"No sets found in local DJ page")

    # --- DJ URL mode: fetch DJ page directly, extract set URLs, scrape them ---
    if args.dj_url:
        for dj_entry in args.dj_url.split(","):
            dj_entry = dj_entry.strip()
            # Derive artist name from URL slug: /dj/enzosiragusa/index.html -> enzosiragusa
            slug = dj_entry.rstrip("/").split("/dj/")[-1].split("/")[0] if "/dj/" in dj_entry else "unknown"
            log.info(f"Fetching DJ page: {dj_entry}")
            dj_html = fetch_html(dj_entry)
            if not dj_html:
                log.error(f"  Failed to fetch DJ page: {dj_entry}")
                continue
            tracklist_urls = get_tracklist_urls_from_html(dj_html, slug, max_sets=args.sets)
            if not tracklist_urls:
                log.warning(f"  No sets found on DJ page: {dj_entry}")
                continue
            for i, url in enumerate(tracklist_urls, 1):
                log.info(f"\n[{slug}] Scraping set {i}/{len(tracklist_urls)}: {url}")
                html, filepath = fetch_and_save(url, output_dir=args.save_html)
                if html:
                    source = os.path.basename(filepath) if filepath else url
                    tracks = extract_tracks(html, source=source)
                    all_tracks.extend(tracks)
                    log.info(f"  Got {len(tracks)} tracks")
                if i < len(tracklist_urls):
                    time.sleep(2)

    # --- Artist search mode: name -> search 1001TL -> DJ page -> sets -> tracks ---
    if args.artist:
        artist_names = [a.strip() for a in args.artist.split(",") if a.strip()]
        for artist_name in artist_names:
            tracklist_urls = search_and_get_sets(artist_name, max_sets=args.sets)
            if not tracklist_urls:
                continue
            for i, url in enumerate(tracklist_urls, 1):
                log.info(f"\n[{artist_name}] Scraping set {i}/{len(tracklist_urls)}: {url}")
                html, filepath = fetch_and_save(url, output_dir=args.save_html)
                if html:
                    source = os.path.basename(filepath) if filepath else url
                    tracks = extract_tracks(html, source=source)
                    all_tracks.extend(tracks)
                    log.info(f"  Got {len(tracks)} tracks")
                # Be polite between requests
                if i < len(tracklist_urls):
                    time.sleep(2)

    # --- Local mode: extract from saved HTML ---
    if args.local:
        path = args.local
        if os.path.isdir(path):
            for fname in sorted(os.listdir(path)):
                if fname.endswith(".html"):
                    filepath = os.path.join(path, fname)
                    log.info(f"Extracting from {filepath}")
                    with open(filepath, "r", encoding="utf-8") as f:
                        html = f.read()
                    tracks = extract_tracks(html, source=fname)
                    all_tracks.extend(tracks)
        elif os.path.isfile(path):
            log.info(f"Extracting from {path}")
            with open(path, "r", encoding="utf-8") as f:
                html = f.read()
            all_tracks.extend(extract_tracks(html, source=os.path.basename(path)))
        else:
            log.error(f"Path not found: {path}")
            sys.exit(1)

    # --- Direct URL mode: fetch via Bright Data ---
    for url in (args.urls or []):
        html, filepath = fetch_and_save(url, output_dir=args.save_html)
        if html:
            source = os.path.basename(filepath) if filepath else url
            tracks = extract_tracks(html, source=source)
            all_tracks.extend(tracks)
            log.info(f"  Found {len(tracks)} tracks from {url}")

    if not all_tracks:
        log.error("No tracks extracted from any source")
        sys.exit(1)

    # Deduplicate
    seen = set()
    unique_tracks = []
    for artist, title, source in all_tracks:
        key = (artist.lower(), title.lower()) if artist != "spotify_uri" else (artist, title)
        if key not in seen:
            seen.add(key)
            unique_tracks.append((artist, title, source))

    log.info(f"\nTotal: {len(unique_tracks)} unique tracks from {len(all_tracks)} raw")

    # Save CSV
    save_tracks_csv(unique_tracks, args.output)

    # Spotify playlist — create first, then search+add in batches
    if args.playlist:
        log.info(f"\nCreating Spotify playlist '{args.playlist}'...")
        sp = get_spotify()
        cache = _load_spotify_cache()
        cached_hits = sum(1 for a, t, _ in unique_tracks if _cache_key(a, clean_track_name(a, t)) in cache)
        log.info(f"  {cached_hits}/{len(unique_tracks)} tracks already cached, {len(unique_tracks) - cached_hits} need searching")

        # Step 1: Create/find playlist FIRST
        playlist_id, playlist_url = _get_or_create_playlist(sp, args.playlist)
        if not playlist_id:
            log.error("Failed to create playlist. Tracks saved to CSV.")
        else:
            log.info(f"  Playlist ready: {playlist_url}")

            # Step 2: Search in batches, add to playlist after each batch
            batch_ids = []
            total_found = 0
            not_found = 0
            for i, (artist, title, _) in enumerate(unique_tracks, 1):
                display = title if artist == "spotify_uri" else f"{artist} - {title}"
                try:
                    tid = find_spotify_track(sp, artist, clean_track_name(artist, title), cache)
                except SpotifyRateLimited:
                    log.error(f"\n  Rate limited at track {i}/{len(unique_tracks)}!")
                    _save_spotify_cache(cache)
                    # Add whatever we have in this batch
                    if batch_ids:
                        _add_tracks_to_playlist(sp, playlist_id, batch_ids)
                        total_found += len(batch_ids)
                    log.info(f"  Saved {total_found} tracks to playlist before rate limit.")
                    log.info(f"  Run again later to continue — cached tracks won't re-search.")
                    break
                if tid:
                    batch_ids.append(tid)
                    if i % 25 == 0 or i <= 3:
                        log.info(f"  [{i}/{len(unique_tracks)}] Found: {display}")
                else:
                    not_found += 1
                    if not_found <= 10:
                        log.warning(f"  [{i}/{len(unique_tracks)}] Not found: {display}")

                # End of batch: add to playlist, save cache, pause
                if len(batch_ids) >= BATCH_SIZE:
                    _add_tracks_to_playlist(sp, playlist_id, batch_ids)
                    total_found += len(batch_ids)
                    batch_ids = []
                    _save_spotify_cache(cache)
                    log.info(f"  Batch done — {total_found} tracks in playlist. Pausing {BATCH_PAUSE}s...")
                    time.sleep(BATCH_PAUSE)
                elif artist != "spotify_uri" and _cache_key(artist, clean_track_name(artist, title)) not in cache:
                    pass  # cache miss was just searched — already delayed by API call time
                    time.sleep(TRACK_DELAY)
            else:
                # Loop completed without rate limit — add final batch
                if batch_ids:
                    _add_tracks_to_playlist(sp, playlist_id, batch_ids)
                    total_found += len(batch_ids)
                _save_spotify_cache(cache)
                log.info(f"\n  Done! {total_found} tracks in playlist, {not_found} not found on Spotify")

            log.info(f"\nPlaylist: {playlist_url}")


if __name__ == "__main__":
    try:
        main()
    finally:
        _close_browser()
