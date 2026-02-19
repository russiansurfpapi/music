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
import logging
import argparse
from urllib.parse import quote_plus
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

BRIGHT_DATA_API_KEY = os.getenv("BRIGHT_DATA_API_KEY")
BRIGHTDATA_ZONE = os.getenv("BRIGHTDATA_ZONE", "web_unlocker1")
BRIGHTDATA_API_URL = "https://api.brightdata.com/request"

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. FETCH — Bright Data with rendering + CAPTCHA bypass
# ---------------------------------------------------------------------------

def fetch_html(url, retries=2, delay=5):
    """Fetch a 1001Tracklists page via Bright Data web unlocker."""
    if not BRIGHT_DATA_API_KEY:
        log.error("BRIGHT_DATA_API_KEY not set in .env")
        return None

    for attempt in range(1, retries + 1):
        log.info(f"[{attempt}/{retries}] Fetching {url}")
        try:
            resp = requests.post(
                BRIGHTDATA_API_URL,
                headers={"Authorization": f"Bearer {BRIGHT_DATA_API_KEY}"},
                json={
                    "zone": BRIGHTDATA_ZONE,
                    "url": url,
                    "format": "raw",
                    "render": True,   # headless Chrome — executes JS, solves CAPTCHAs
                },
                timeout=120,
            )
            if resp.status_code == 200:
                html = resp.text
                # Quick sanity check — did we get a CAPTCHA page anyway?
                if "Error 403" in html[:2000] or "cf-challenge" in html[:5000]:
                    log.warning("Got a CAPTCHA/challenge page, retrying...")
                    time.sleep(delay)
                    continue
                return html
            else:
                log.warning(f"Bright Data returned {resp.status_code}: {resp.text[:200]}")
        except requests.RequestException as e:
            log.warning(f"Request failed: {e}")

        if attempt < retries:
            time.sleep(delay)

    log.error(f"Failed to fetch {url} after {retries} attempts")
    return None


def fetch_and_save(url, output_dir="sets"):
    """Fetch a URL and save the HTML to disk."""
    html = fetch_html(url)
    if not html:
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


def search_artist(artist_name):
    """
    Search 1001Tracklists for an artist.
    Returns (dj_page_html, dj_url, dj_name) or None.
    Caches the HTML so we don't double-fetch.
    """
    log.info(f"\nSearching 1001Tracklists for '{artist_name}'...")
    search_url = f"{BASE_URL}/search/result.php?search_selection=1&main_search={quote_plus(artist_name)}"
    html = fetch_html(search_url)
    if not html:
        return _guess_dj_url(artist_name)

    soup = BeautifulSoup(html, "html.parser")

    # Look for DJ profile links in search results
    dj_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/dj/" in href and href.endswith("/index.html"):
            full = href if href.startswith("http") else BASE_URL + href
            name = a.get_text(strip=True)
            dj_links.append((full, name))

    if not dj_links:
        # Sometimes results are just tracklist links — collect those directly
        tracklist_urls = _extract_tracklist_links(soup, artist_name)
        if tracklist_urls:
            log.info(f"  No DJ page found, but found {len(tracklist_urls)} tracklist links in search results")
            return ("search_results_html", "search_results", tracklist_urls)

        log.info("  No results from search, trying URL guess...")
        return _guess_dj_url(artist_name)

    # Pick best match — prefer exact/close name match
    artist_lower = artist_name.strip().lower()
    best = None
    for url, name in dj_links:
        if name.strip().lower() == artist_lower:
            best = (url, name)
            break
    if not best:
        best = dj_links[0]

    # Fetch the DJ page now and cache the HTML
    log.info(f"  Found DJ page: {best[1]} -> {best[0]}")
    dj_html = fetch_html(best[0])
    return (dj_html, best[0], best[1]) if dj_html else None


def _guess_dj_url(artist_name):
    """Guess the DJ profile URL from the artist name slug."""
    slug = _artist_slug(artist_name)  # 'Sister Zo' -> 'sister-zo'
    url = f"{BASE_URL}/dj/{slug}/index.html"
    log.info(f"  Guessing DJ URL: {url}")
    html = fetch_html(url)
    if html and "404" not in html[:500]:
        return (html, url, artist_name)
    return None


def get_tracklist_urls_from_html(html, artist_name, max_sets=10):
    """
    Parse a DJ profile page HTML and extract tracklist URLs.
    Filters to only include the artist's OWN sets (by matching URL slug).
    """
    soup = BeautifulSoup(html, "html.parser")
    all_urls = _extract_tracklist_links(soup, artist_name)

    if all_urls:
        log.info(f"  Found {len(all_urls)} sets by {artist_name}, using top {min(len(all_urls), max_sets)}")
    else:
        log.warning(f"  No tracklist links found for {artist_name}")

    return all_urls[:max_sets]


def _extract_tracklist_links(soup, artist_name=""):
    """
    Pull /tracklist/ links from a parsed page.
    Checks both <a href> tags AND onclick/JS handlers (1001TL uses both).
    When artist_name is provided, prioritize links whose URL contains the artist slug.
    """
    slug = _artist_slug(artist_name) if artist_name else ""
    seen = set()
    own_sets = []
    other_sets = []

    raw_html = str(soup)

    # Method 1: <a href="/tracklist/...">
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/tracklist/" in href and href.endswith(".html"):
            full = href if href.startswith("http") else BASE_URL + href
            if full not in seen:
                seen.add(full)

    # Method 2: JS onclick handlers and inline scripts
    # e.g. "ferra-black-some-mix-2021.html', '_self');"
    # or "/tracklist/28z748yt/ferra-black-some-mix.html"
    for match in re.findall(r'/tracklist/[a-z0-9]+/[a-z0-9_-]+\.html', raw_html):
        full = BASE_URL + match
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
        return own_sets
    # Fall back to all sets if no slug match
    return own_sets + other_sets


def search_and_get_sets(artist_name, max_sets=10):
    """
    Full pipeline: artist name -> search 1001TL -> DJ page -> tracklist URLs.
    Returns list of tracklist URLs to scrape.
    """
    result = search_artist(artist_name)
    if not result:
        log.error(f"  Could not find '{artist_name}' on 1001Tracklists")
        return []

    dj_html, dj_url, name_or_urls = result

    # If search returned tracklist URLs directly (no DJ page found)
    if dj_url == "search_results":
        urls = name_or_urls[:max_sets]
        log.info(f"  Using {len(urls)} tracklists from search results")
        return urls

    # Parse the cached DJ page HTML — no re-fetch needed
    return get_tracklist_urls_from_html(dj_html, artist_name, max_sets=max_sets)


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
    """Lazy-init Spotify client (only when needed)."""
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth

    return spotipy.Spotify(auth_manager=SpotifyOAuth(
        scope="playlist-modify-public playlist-modify-private",
        client_id=os.getenv("SPOTIPY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
        redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
    ))


def find_spotify_track(sp, artist, title):
    """Search Spotify for a single track. Returns track ID or None."""
    if artist == "spotify_uri":
        # Already have the URI
        track_id = title.replace("spotify:track:", "")
        return track_id

    queries = [
        f"track:{title} artist:{artist}",
        f"{artist} {title}",
        f"{title} {artist}",
    ]
    for q in queries:
        try:
            results = sp.search(q=q, type="track", limit=1)
            items = results.get("tracks", {}).get("items", [])
            if items:
                return items[0]["id"]
        except Exception:
            continue
    return None


def create_spotify_playlist(sp, track_ids, playlist_name="Escuchar"):
    """Create (or find) a playlist and add tracks."""
    user_id = sp.current_user()["id"]

    # Find existing playlist
    playlist_id = None
    playlist_url = None
    offset = 0
    while True:
        page = sp.current_user_playlists(limit=50, offset=offset)
        for pl in page["items"]:
            if pl and pl.get("name", "").strip().lower() == playlist_name.strip().lower():
                playlist_id = pl["id"]
                playlist_url = pl["external_urls"]["spotify"]
                break
        if playlist_id or len(page["items"]) < 50:
            break
        offset += 50

    if not playlist_id:
        new_pl = sp.user_playlist_create(user=user_id, name=playlist_name, public=True)
        playlist_id = new_pl["id"]
        playlist_url = new_pl["external_urls"]["spotify"]
        log.info(f"Created playlist: {playlist_name}")

    # Get existing track IDs to avoid duplicates
    existing = set()
    offset = 0
    while True:
        resp = sp.playlist_tracks(playlist_id, fields="items.track.id", limit=100, offset=offset)
        items = resp.get("items", [])
        if not items:
            break
        for item in items:
            if item and item.get("track") and item["track"].get("id"):
                existing.add(item["track"]["id"])
        offset += 100
        if len(items) < 100:
            break

    new_ids = [tid for tid in track_ids if tid and tid not in existing]
    if new_ids:
        for i in range(0, len(new_ids), 100):
            sp.playlist_add_items(playlist_id, new_ids[i:i+100])
        log.info(f"Added {len(new_ids)} tracks to playlist")
    else:
        log.info("All tracks already in playlist")

    return playlist_url


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

    # Spotify playlist
    if args.playlist:
        log.info(f"\nSearching Spotify and creating playlist '{args.playlist}'...")
        sp = get_spotify()
        track_ids = []
        for i, (artist, title, _) in enumerate(unique_tracks, 1):
            display = title if artist == "spotify_uri" else f"{artist} - {title}"
            tid = find_spotify_track(sp, artist, clean_track_name(artist, title))
            if tid:
                track_ids.append(tid)
                log.info(f"  [{i}/{len(unique_tracks)}] Found: {display}")
            else:
                log.warning(f"  [{i}/{len(unique_tracks)}] Not found: {display}")

        if track_ids:
            url = create_spotify_playlist(sp, track_ids, args.playlist)
            log.info(f"\nPlaylist: {url}")
        else:
            log.error("No tracks found on Spotify")


if __name__ == "__main__":
    main()
