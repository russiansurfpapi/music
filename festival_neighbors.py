#!/usr/bin/env python3
"""
Festival Neighbors — discover who's playing alongside an artist, sample everyone.

Usage:
  python3 festival_neighbors.py --festival "Primavera Sound 2026"
  python3 festival_neighbors.py --festival "Sonar 2026"
  python3 festival_neighbors.py --artist "Peggy Gou"
  python3 festival_neighbors.py --url "https://en.wikipedia.org/wiki/Primavera_Sound_2026"
  python3 festival_neighbors.py --lineup lineup.txt
  python3 festival_neighbors.py --festival "Primavera Sound 2026" --dry-run
  python3 festival_neighbors.py --festival "Primavera Sound 2026" --tracks 5
"""

import argparse
import json
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

import spotipy
import spotify_guard  # noqa: F401 — charges every request to the daily budget
from spotipy.oauth2 import SpotifyOAuth

SCOPE = "playlist-modify-public playlist-modify-private"
TRACK_DELAY = 4
BATCH_PAUSE = 30

# ---------------------------------------------------------------------------
# Spotify helpers (dev-mode safe)
# ---------------------------------------------------------------------------

def _get_sp():
    return spotipy.Spotify(
        auth_manager=SpotifyOAuth(scope=SCOPE, cache_path=".cache"),
        requests_session=requests.Session(),
    )


def _create_playlist(sp, name, description=""):
    token = sp.auth_manager.get_access_token(as_dict=False)
    r = requests.post(
        "https://api.spotify.com/v1/me/playlists",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"name": name, "description": description, "public": True},
    )
    if r.status_code == 201:
        pl = r.json()
        return pl["id"], pl["external_urls"]["spotify"]
    print(f"  Failed to create playlist: {r.status_code} {r.text[:200]}")
    return None, None


def _search_artist_tracks(sp, artist_name, limit=3):
    """Search-based top tracks (artist_top_tracks returns 403 in dev mode)."""
    try:
        r = sp.search(q=f"artist:{artist_name}", type="track", limit=limit + 5)
        items = r["tracks"]["items"]
        artist_lower = artist_name.lower()
        matched = []
        for t in items:
            for a in t["artists"]:
                if artist_lower in a["name"].lower() or a["name"].lower() in artist_lower:
                    matched.append(t)
                    break
        results = matched[:limit] if matched else items[:limit]
        return [(t["id"], f"{t['artists'][0]['name']} - {t['name']}") for t in results]
    except Exception as e:
        if "429" in str(e):
            raise
        return []


def _add_tracks(sp, playlist_id, track_ids):
    seen = set()
    unique = [t for t in track_ids if t and not (t in seen or seen.add(t))]
    for i in range(0, len(unique), 100):
        batch = unique[i : i + 100]
        sp.playlist_add_items(playlist_id, [f"spotify:track:{t}" for t in batch])
    return len(unique)


# ---------------------------------------------------------------------------
# Web search + scrape
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def _web_search(query, max_results=10):
    """Search Google via SerpAPI. Clean structured results, no scraping needed."""
    serp_key = os.getenv("SERPAPI_KEY")
    results = []

    if serp_key:
        try:
            r = requests.get(
                "https://serpapi.com/search.json",
                params={"q": query, "num": max_results, "api_key": serp_key},
                timeout=30,
            )
            if r.status_code == 200:
                data = r.json()
                for item in data.get("organic_results", []):
                    results.append((
                        item.get("title", ""),
                        item.get("link", ""),
                        item.get("snippet", ""),
                    ))
                return results[:max_results]
        except Exception as e:
            print(f"  SerpAPI search failed: {e}")

    # Fallback: direct Google (usually blocked but worth trying)
    from urllib.parse import quote_plus
    try:
        url = f"https://www.google.com/search?q={quote_plus(query)}&num={max_results}"
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            for h3 in soup.select("h3"):
                parent_a = h3.find_parent("a")
                if parent_a:
                    href = parent_a.get("href", "")
                    if href.startswith("/url?q="):
                        href = href.split("/url?q=")[1].split("&")[0]
                    if href.startswith("http"):
                        results.append((h3.get_text(), href, ""))
    except Exception as e:
        print(f"  Google search failed: {e}")
    return results


def _fetch_page(url):
    """Fetch a page and return text content."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"  Failed to fetch {url}: {e}")
        return None


# ---------------------------------------------------------------------------
# Artist extraction
# ---------------------------------------------------------------------------

def _extract_artists_from_wikipedia(html):
    """Extract artist names from a Wikipedia festival page."""
    soup = BeautifulSoup(html, "html.parser")
    artists = set()

    for a in soup.select("td a, li a, dd a"):
        href = a.get("href", "")
        if "/wiki/" not in href:
            continue
        # Skip non-artist links
        skip_patterns = [
            "Category:", "Template:", "Special:", "Help:", "Wikipedia:",
            "File:", "Talk:", "Portal:", "Module:", "#cite",
            "Parc_del_", "Barcelona", "Spain", "Catalonia",
            "Music_festival", "Wikimedia", "Creative_Commons",
            "Privacy_policy", "Terms_of_Use", "Primavera_Sound_2",
            "Summer_Olympics", "Stereogum", "Rolling_Stone", "NME",
            "BrooklynVegan", "The_Independent", "Relix", "IQ_(",
            "Geographic_coordinate", "Parsoid", "MediaWiki",
            "Yucaipa", "Ronald_Burkle", "Rockdelux",
            "Main_Page", "Cookie_statement",
        ]
        if any(p in href for p in skip_patterns):
            continue
        # Skip venue/location links
        venue_patterns = [
            "Forum", "Razzmatazz", "Apolo", "Poble_Espanyol",
            "Museum_of", "Centre_de_Cultura",
        ]
        if any(p in href for p in venue_patterns):
            continue

        text = a.get_text().strip()
        if not text or len(text) < 2 or len(text) > 60:
            continue
        # Skip dates, generic terms, genres, navigation
        skip_texts = {
            "edit", "v", "t", "e", "main page", "contents", "current events",
            "random article", "about wikipedia", "contact us", "help",
            "indie rock", "post-punk", "shoegaze", "hyperpop", "pop music",
            "electronic music", "hip hop", "techno", "house music",
            "rock music", "jazz", "reggaeton", "r&b",
        }
        if re.match(r"^\d{4}$", text) or text.lower().strip() in skip_texts:
            continue
        if len(text.split()) == 1 and text.lower() in {
            "rock", "pop", "jazz", "soul", "funk", "metal", "punk",
            "techno", "house", "trance", "dubstep", "reggae",
        }:
            continue

        artists.add(text)

    return sorted(artists)


def _extract_artists_with_llm(text):
    """Use Claude to extract artist names from lineup text."""
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

        prompt = f"""Extract the names of musical artists and performers from this festival lineup text.

RULES:
1. Include ONLY musicians, DJs, bands, and musical performers
2. EXCLUDE talks, panels, workshops, venues, stages, sponsors, dates, locations, news outlets
3. Clean up names: remove "(Live)", "(cancelled)", "(DJ set)", work titles after colons
4. Return ONLY a JSON array of artist names, nothing else

Text:
{text[:8000]}"""

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        result = response.content[0].text.strip()
        result = re.sub(r"^```(?:json)?\s*\n", "", result)
        result = re.sub(r"\n```\s*$", "", result)
        artists = json.loads(result.strip())
        seen = set()
        unique = []
        for a in artists:
            norm = a.lower().strip()
            if norm and len(norm) > 1 and norm not in seen:
                seen.add(norm)
                unique.append(a)
        return unique
    except Exception as e:
        print(f"  LLM extraction failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Discovery modes
# ---------------------------------------------------------------------------

def _guess_wikipedia_url(festival_name):
    """Construct likely Wikipedia URL from festival name."""
    from urllib.parse import quote
    name = festival_name.strip()
    slug = name.replace(" ", "_")
    candidates = [
        f"https://en.wikipedia.org/wiki/{quote(slug)}",
    ]
    # "Primavera Sound 2026" -> already good
    # "Sonar 2026" -> "Sónar_2026" (might need accent)
    # "Coachella 2026" -> "Coachella_Valley_Music_and_Arts_Festival_2026" (too complex)
    # Try as-is first, then with "_(festival)" suffix
    if not any(c in name.lower() for c in ["festival", "sound", "lands", "chella"]):
        candidates.append(f"https://en.wikipedia.org/wiki/{quote(slug)}_(festival)")
    return candidates


def discover_from_festival(festival_name):
    """Search for a festival lineup and extract artists."""
    print(f"\nSearching for: {festival_name} lineup...")

    # Fast path: try Wikipedia directly (no search needed)
    for wiki_url in _guess_wikipedia_url(festival_name):
        html = _fetch_page(wiki_url)
        if html and "does not have an article" not in html:
            artists = _extract_artists_from_wikipedia(html)
            if 5 <= len(artists) <= 300:
                print(f"  Found {len(artists)} artists via Wikipedia: {wiki_url}")
                return artists, wiki_url
            elif len(artists) > 300:
                print(f"  Skipping {wiki_url} — {len(artists)} links, likely a general page")

    # Try Google search for Wikipedia page
    wiki_query = f"{festival_name} lineup site:en.wikipedia.org"
    results = _web_search(wiki_query, 5)

    # Search for Wikipedia page — but verify it's about the right festival
    fest_words = set(re.sub(r"\d{4}", "", festival_name).lower().split()) - {"the", "of", "and", "in", "at", "a"}
    for title, url, _ in results:
        if "wikipedia.org/wiki/" not in url:
            continue
        # Check URL slug contains at least one word from the festival name
        url_slug = url.split("/wiki/")[-1].lower().replace("_", " ")
        if not any(w in url_slug for w in fest_words if len(w) > 3):
            continue
        print(f"  Found Wikipedia via search: {url}")
        html = _fetch_page(url)
        if html:
            artists = _extract_artists_from_wikipedia(html)
            if 5 <= len(artists) <= 200:
                print(f"  Extracted {len(artists)} artists from Wikipedia")
                return artists, url
            elif len(artists) > 200:
                print(f"  Too many links ({len(artists)}) — likely a general page")

    # Try Bright Data scrape for harder-to-reach pages
    bd_api_key = os.getenv("BRIGHT_DATA_API_KEY")
    fallback_urls = []

    # Build candidate URLs
    from urllib.parse import quote_plus
    mfw_slug = festival_name.lower().replace(" ", "-")
    fallback_urls.append(f"https://www.musicfestivalwizard.com/festivals/{quote_plus(mfw_slug)}/")

    # Try Jambase
    fallback_urls.append(f"https://www.jambase.com/festival/{mfw_slug}")

    for furl in fallback_urls:
        html = _fetch_page(furl)
        if html and len(html) > 2000:
            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text(separator="\n")
            artists = _extract_artists_with_llm(text)
            if len(artists) >= 3:
                print(f"  Extracted {len(artists)} artists from {furl}")
                return artists, furl

    # Bright Data scrape fallback (handles bot detection)
    if bd_api_key:
        print("  Trying Bright Data scrape...")
        for furl in fallback_urls:
            try:
                r = requests.post(
                    "https://api.brightdata.com/request",
                    headers={"Authorization": f"Bearer {bd_api_key}", "Content-Type": "application/json"},
                    json={"zone": os.getenv("BRIGHTDATA_ZONE", "web_unlocker1"), "url": furl, "format": "raw"},
                    timeout=60,
                )
                if r.status_code == 200 and len(r.text) > 2000:
                    soup = BeautifulSoup(r.text, "html.parser")
                    text = soup.get_text(separator="\n")
                    artists = _extract_artists_with_llm(text)
                    if len(artists) >= 3:
                        print(f"  Extracted {len(artists)} artists via Bright Data from {furl}")
                        return artists, furl
            except Exception as e:
                print(f"  Bright Data failed for {furl}: {e}")

    # Fall back to general Google search + LLM extraction
    print("  Trying general search...")
    results = _web_search(f"{festival_name} full lineup", 5)
    for title, url, _ in results[:3]:
        html = _fetch_page(url)
        if html:
            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text(separator="\n")
            artists = _extract_artists_with_llm(text)
            if len(artists) >= 3:
                print(f"  Extracted {len(artists)} artists from {url}")
                return artists, url

    print("  Could not find lineup automatically.")
    print("  Try: --url <lineup_page_url>  or  --lineup <text_file>")
    return [], None


def discover_from_artist(artist_name):
    """Find festivals an artist is playing, then get the lineup."""
    print(f"\nSearching for festivals featuring: {artist_name}...")

    query = f"{artist_name} festival 2026 lineup"
    results = _web_search(query, 10)

    festivals = []
    seen = set()

    # Known festival name patterns
    KNOWN_FESTS = [
        "Primavera Sound", "Coachella", "Glastonbury", "Bonnaroo",
        "Lollapalooza", "Sonar", "Tomorrowland", "EXIT", "Panorama",
        "Ultra", "EDC", "SXSW", "Governors Ball", "Pitchfork",
        "Wireless", "Reading", "Leeds", "Roskilde", "Fuji Rock",
        "Dekmantel", "Melt", "Lovebox", "All Points East",
    ]

    for title, url, _ in results:
        title_lower = title.lower()

        # Check for known festival names in title
        for fest in KNOWN_FESTS:
            if fest.lower() in title_lower:
                # Try to find year
                year_match = re.search(r"20\d{2}", title)
                year = year_match.group(0) if year_match else "2026"
                name = f"{fest} {year}"
                if name.lower() not in seen:
                    seen.add(name.lower())
                    festivals.append(name)

        # Generic festival name extraction
        for pattern in [
            r"([\w'-]+(?:\s+[\w'-]+){0,3}\s+Festival)\s*(20\d{2})?",
            r"([\w'-]+\s+Sound)\s*(20\d{2})?",
            r"([\w'-]+\s+Fest)\s*(20\d{2})?",
        ]:
            for m in re.finditer(pattern, title, re.IGNORECASE):
                name = m.group(1).strip()
                year = m.group(2) or "2026"
                full = f"{name} {year}"
                if full.lower() not in seen and len(name) > 4:
                    # Skip noise like "Tour Dates Festival"
                    if any(skip in name.lower() for skip in ["tour", "ticket", "date", "guide"]):
                        continue
                    seen.add(full.lower())
                    festivals.append(full)

    if not festivals:
        # Broader search
        query2 = f"{artist_name} festival summer 2025 2026"
        results2 = _web_search(query2, 10)
        for title, url, _ in results2:
            if "festival" in title.lower() or "lineup" in title.lower():
                # Take the whole title as a hint
                festivals.append(title[:60])
                if len(festivals) >= 3:
                    break

    if not festivals:
        print("  No festivals found. Try --festival 'Festival Name' instead.")
        return [], None

    print(f"  Found {len(festivals)} potential festivals:")
    for f in festivals[:5]:
        print(f"    - {f}")

    # Collect URLs from search results that likely contain lineup info
    lineup_urls = []
    skip_domains = ["songkick.com", "ticketmaster", "bandsintown", "spotify.com", "instagram.com", "ra.co"]
    for title, url, snippet in results:
        if any(skip in url for skip in skip_domains):
            continue
        title_lower = title.lower()
        if any(k in title_lower for k in ["lineup", "announces", "unveils", "headlin"]):
            lineup_urls.append((title, url, snippet))

    # Strategy 1: scrape lineup URLs from search results directly
    #   (these often contain the full lineup in the article text)
    all_artists = []
    source_url = None
    for title, url, snippet in lineup_urls[:4]:
        print(f"  Trying: {title[:60]}...")
        html = _fetch_page(url)
        if html and len(html) > 1000:
            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text(separator="\n")
            artists = _extract_artists_with_llm(text[:8000])
            if len(artists) >= 5:
                all_artists = artists
                source_url = url
                print(f"    -> {len(artists)} artists extracted")
                break

    # Strategy 2: try discover_from_festival for named festivals
    if not all_artists:
        for fest in festivals[:2]:
            artists, url = discover_from_festival(fest)
            if artists and 5 <= len(artists) <= 200:
                all_artists = artists
                source_url = url
                print(f"\n  Using lineup from: {fest}")
                break

    # Remove the search artist from the lineup
    artist_lower = artist_name.lower()
    all_artists = [a for a in all_artists if artist_lower not in a.lower()]

    return all_artists, source_url


def discover_from_url(url):
    """Scrape a specific URL for lineup data."""
    print(f"\nScraping: {url}")
    html = _fetch_page(url)
    if not html:
        return [], url

    if "wikipedia.org" in url:
        artists = _extract_artists_from_wikipedia(html)
    else:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator="\n")
        artists = _extract_artists_with_llm(text)

    print(f"  Extracted {len(artists)} artists")
    return artists, url


def discover_from_file(filepath):
    """Read lineup from a text file."""
    with open(filepath) as f:
        text = f.read()
    artists = _extract_artists_with_llm(text)
    print(f"  Extracted {len(artists)} artists from {filepath}")
    return artists, filepath


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def build_sampler(artists, playlist_name, tracks_per_artist=3, dry_run=False, exclude=None):
    """Sample each artist on Spotify and build a playlist."""
    if exclude:
        exclude_lower = {e.strip().lower() for e in exclude}
        artists = [a for a in artists if a.lower() not in exclude_lower]

    print(f"\n{'='*60}")
    print(f"Sampling {len(artists)} artists ({tracks_per_artist} tracks each)")
    print(f"{'='*60}\n")

    if dry_run:
        for i, a in enumerate(artists, 1):
            print(f"  {i:3}. {a}")
        print(f"\n  Total: {len(artists)} artists, ~{len(artists) * tracks_per_artist} tracks")
        print("  (dry run — no Spotify calls)")
        return

    sp = _get_sp()
    all_track_ids = []
    found = 0
    missed = []

    for i, artist in enumerate(artists, 1):
        print(f"[{i}/{len(artists)}] {artist}", end="")
        results = _search_artist_tracks(sp, artist, tracks_per_artist)
        if results:
            found += 1
            for tid, desc in results:
                all_track_ids.append(tid)
            print(f"  -> {len(results)} tracks")
        else:
            missed.append(artist)
            print(f"  -> not found")
        time.sleep(TRACK_DELAY)

        # Check for 429 after each search
        if i % 50 == 0 and i < len(artists):
            print(f"\n  --- pause {BATCH_PAUSE}s (batch cooldown) ---\n")
            time.sleep(BATCH_PAUSE)

    # Dedupe
    seen = set()
    unique = [t for t in all_track_ids if t and not (t in seen or seen.add(t))]

    print(f"\n{'='*60}")
    print(f"Found {found}/{len(artists)} artists, {len(unique)} unique tracks")

    if missed:
        print(f"\nNot found on Spotify ({len(missed)}):")
        for m in missed[:15]:
            print(f"  - {m}")
        if len(missed) > 15:
            print(f"  ... and {len(missed) - 15} more")

    if not unique:
        print("No tracks to add!")
        return

    # Create playlist
    desc = f"Festival sampler: {len(found) if isinstance(found, list) else found} artists, {len(unique)} tracks. Built by festival_neighbors.py"
    pl_id, pl_url = _create_playlist(sp, playlist_name, desc)
    if not pl_id:
        print("Failed to create playlist!")
        return

    added = _add_tracks(sp, pl_id, unique)
    print(f"\nPlaylist: {pl_url}")
    print(f"  {added} tracks added")


def main():
    ap = argparse.ArgumentParser(description="Festival Neighbors — sample every artist on a lineup")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--festival", help="Festival name to search for (e.g. 'Primavera Sound 2026')")
    mode.add_argument("--artist", help="Artist name — find their festivals, sample the lineup")
    mode.add_argument("--url", help="URL of a lineup page to scrape")
    mode.add_argument("--lineup", help="Path to a text file with lineup names")

    ap.add_argument("--tracks", type=int, default=3, help="Tracks per artist (default 3)")
    ap.add_argument("--playlist", default=None, help="Custom playlist name")
    ap.add_argument("--dry-run", action="store_true", help="List artists without creating playlist")
    ap.add_argument("--exclude", default=None, help="Comma-separated artists to skip")

    args = ap.parse_args()

    # Discover artists
    if args.festival:
        artists, source = discover_from_festival(args.festival)
        default_name = f"{args.festival} Sampler"
    elif args.artist:
        artists, source = discover_from_artist(args.artist)
        default_name = f"{args.artist} — Festival Neighbors"
    elif args.url:
        artists, source = discover_from_url(args.url)
        default_name = "Festival Sampler"
    elif args.lineup:
        artists, source = discover_from_file(args.lineup)
        default_name = f"{os.path.basename(args.lineup).split('.')[0]} Sampler"

    if not artists:
        print("\nNo artists found. Try a different search or use --url with a specific lineup page.")
        sys.exit(1)

    playlist_name = args.playlist or default_name
    exclude = args.exclude.split(",") if args.exclude else None

    build_sampler(artists, playlist_name, args.tracks, args.dry_run, exclude)


if __name__ == "__main__":
    main()
