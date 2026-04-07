"""
"Do I Give a Fuck?" Concert Matcher
Matches your Spotify listening history against Ticketmaster NYC concerts.
Completely standalone — no web app dependencies.
"""

from __future__ import annotations

import os
import re
import sys
import time
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

# ─── Config ──────────────────────────────────────────────────────────────────

# Load Spotify creds from Music/.env
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# Load TM API key from putmeon/.env.local (read-only)
_putmeon_env = os.path.join(
    os.path.dirname(__file__), "..", "Coding", "putmeon", "loopedin", ".env.local"
)
if os.path.exists(_putmeon_env):
    load_dotenv(_putmeon_env, override=False)

TM_API_KEY = os.getenv("TICKETMASTER_API_KEY", "")

# Spotify — separate cache file so it doesn't conflict with main.py's playlist scopes
sp = spotipy.Spotify(
    auth_manager=SpotifyOAuth(
        scope="user-library-read user-top-read",
        client_id=os.getenv("SPOTIPY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
        redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
        cache_path=os.path.join(os.path.dirname(__file__), ".cache-matcher"),
    )
)

# NYC bounding box (five boroughs)
_NYC_LAT_MIN, _NYC_LAT_MAX = 40.49, 40.92
_NYC_LNG_MIN, _NYC_LNG_MAX = -74.27, -73.68

# Known NYC venues (from putmeon dedup, copied here for zero coupling)
_NYC_VENUES = {
    "lincoln center", "david geffen hall", "david rubenstein atrium",
    "alice tully hall", "lincoln center theater", "jazz at lincoln center",
    "metropolitan opera", "the met opera", "juilliard",
    "brooklyn steel", "bam", "brooklyn academy of music",
    "st. ann's warehouse", "st ann's warehouse", "national sawdust",
    "music hall of williamsburg", "rough trade", "baby's all right",
    "warsaw", "knitting factory", "elsewhere", "house of yes",
    "knockdown center", "pioneer works", "barclays center",
    "madison square garden", "msg", "radio city", "radio city music hall",
    "beacon theatre", "terminal 5", "webster hall", "irving plaza",
    "bowery ballroom", "mercury lounge", "le poisson rouge",
    "village vanguard", "blue note", "carnegie hall", "town hall",
    "joe's pub", "the public theater", "la mama",
    "kings theatre", "prospect park", "central park",
    "the bell house", "union pool", "public records",
    "now and then", "good room", "basement", "superior ingredients",
    "brooklyn paramount", "forest hills stadium", "summerstage",
    "pier 17", "rooftop at pier 17", "hammerstein ballroom",
    "avant gardner", "the great hall", "kings theater",
    "racket nyc", "sultan room", "nublu", "sony hall",
    "gramercy theatre", "the town hall", "chelsea music hall",
}

SCORE_THRESHOLD = 20  # Minimum "give a fuck" score to query TM
END_DATE = "2026-10-31"


# ─── Helpers ─────────────────────────────────────────────────────────────────

def normalize(name: str) -> str:
    """Normalize artist name for matching: lowercase, strip accents, remove noise."""
    name = name.strip().lower()
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    name = re.sub(r"\s*\(.*?\)\s*", " ", name)
    name = re.sub(
        r"\s*[-–—:]\s*(tour|live|dj set|presents|album release|record release|headline|in concert|concert).*$",
        "",
        name,
        flags=re.IGNORECASE,
    )
    name = re.sub(r"^the\s+", "", name)
    return name.strip()


def _is_nyc_venue(event: Dict) -> bool:
    """Return True if the TM event's venue is within NYC proper."""
    venues = event.get("_embedded", {}).get("venues", [])
    if not venues:
        return False

    v = venues[0]

    # State check
    state = v.get("state", {}).get("stateCode", "")
    if state and state != "NY":
        return False

    # Lat/lng bounding box
    location = v.get("location", {})
    lat_str = location.get("latitude", "")
    lng_str = location.get("longitude", "")
    if lat_str and lng_str:
        try:
            lat, lng = float(lat_str), float(lng_str)
            return _NYC_LAT_MIN <= lat <= _NYC_LAT_MAX and _NYC_LNG_MIN <= lng <= _NYC_LNG_MAX
        except (ValueError, TypeError):
            pass

    # Known venue whitelist fallback
    venue_name = v.get("name", "").strip().lower()
    if venue_name:
        if venue_name in _NYC_VENUES:
            return True
        for known in _NYC_VENUES:
            if known in venue_name:
                return True

    return False


def _extract_event_info(event: Dict) -> Dict:
    """Pull relevant fields from a TM event."""
    venues = event.get("_embedded", {}).get("venues", [])
    venue_name = venues[0].get("name", "") if venues else ""

    # Price range
    price_ranges = event.get("priceRanges", [])
    price_str = ""
    if price_ranges:
        p = price_ranges[0]
        lo = p.get("min", "")
        hi = p.get("max", "")
        if lo and hi:
            price_str = f"${lo:.0f}-${hi:.0f}"
        elif lo:
            price_str = f"${lo:.0f}+"

    return {
        "date": (event.get("dates", {}).get("start", {}).get("localDate", "")),
        "venue": venue_name,
        "url": event.get("url", ""),
        "price": price_str,
        "name": event.get("name", ""),
    }


# ─── Step 1: Spotify → "Give a Fuck" list ───────────────────────────────────

def get_saved_tracks_artists() -> Dict[str, int]:
    """Fetch all saved tracks and count tracks per artist."""
    print("Fetching your saved tracks...")
    artist_counts: Dict[str, int] = {}
    offset = 0
    total = None

    while True:
        results = sp.current_user_saved_tracks(limit=50, offset=offset)
        if total is None:
            total = results["total"]
            print(f"   {total} saved tracks")

        for item in results["items"]:
            track = item.get("track")
            if not track:
                continue
            for artist in track.get("artists", []):
                name = artist["name"]
                artist_counts[name] = artist_counts.get(name, 0) + 1

        offset += 50
        if offset >= total:
            break
        if offset % 500 == 0:
            print(f"   ...{offset}/{total}")

    print(f"   {len(artist_counts)} unique artists")
    return artist_counts


def get_top_artists() -> Dict[str, float]:
    """Fetch top artists across all time ranges with weighted scores."""
    print("Fetching your top artists...")
    scores: Dict[str, float] = {}

    ranges = [
        ("short_term", 150),   # Recent listening — highest weight
        ("medium_term", 100),  # Last ~6 months
        ("long_term", 50),     # All time
    ]

    for time_range, base_score in ranges:
        results = sp.current_user_top_artists(limit=50, time_range=time_range)
        artists = results["items"]
        print(f"   {time_range}: {len(artists)} artists")
        for i, artist in enumerate(artists):
            name = artist["name"]
            rank_score = base_score - i
            scores[name] = scores.get(name, 0) + rank_score

    print(f"   {len(scores)} unique top artists")
    return scores


def build_give_a_fuck_list(
    saved_counts: Dict[str, int], top_scores: Dict[str, float]
) -> Dict[str, float]:
    """Combine saved-track counts and top-artist scores into one ranked dict."""
    all_artists = set(saved_counts.keys()) | set(top_scores.keys())
    scores: Dict[str, float] = {}

    for name in all_artists:
        saved = saved_counts.get(name, 0) * 10  # 10 pts per saved track
        top = top_scores.get(name, 0)
        total = saved + top
        if total >= SCORE_THRESHOLD:
            scores[name] = total

    return scores


# ─── Step 2: Bulk-fetch all NYC music events from Ticketmaster ───────────────

WINDOW_DAYS = 14  # Sliding window size — avoids TM's 1000-result deep paging limit


def fetch_all_nyc_events() -> List[Dict]:
    """Fetch ALL upcoming NYC music events using sliding time windows."""
    from datetime import timedelta

    start = datetime.now()
    end = datetime.strptime(END_DATE, "%Y-%m-%d")
    seen_ids = set()
    all_events = []

    window_start = start
    window_num = 0

    while window_start < end:
        window_end = min(window_start + timedelta(days=WINDOW_DAYS), end)
        window_num += 1
        start_str = window_start.strftime("%Y-%m-%dT00:00:00Z")
        end_str = window_end.strftime("%Y-%m-%dT23:59:59Z")

        page = 0
        total_pages = 1

        while page < total_pages and page < 5:  # TM caps useful pages at ~5
            params = {
                "apikey": TM_API_KEY,
                "dmaId": "345",
                "classificationName": "Music",
                "startDateTime": start_str,
                "endDateTime": end_str,
                "size": 200,
                "page": page,
                "sort": "date,asc",
            }

            try:
                resp = requests.get(
                    "https://app.ticketmaster.com/discovery/v2/events.json",
                    params=params,
                    timeout=30,
                )
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 5))
                    print(f"   Rate limited, waiting {retry_after}s...")
                    time.sleep(retry_after)
                    continue
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"   TM error (window {window_num}, page {page}): {e}")
                break

            page_info = data.get("page", {})
            total_pages = min(page_info.get("totalPages", 0), 5)

            events = data.get("_embedded", {}).get("events", [])
            new_count = 0
            for event in events:
                eid = event.get("id", "")
                if eid in seen_ids:
                    continue
                seen_ids.add(eid)

                if _is_nyc_venue(event):
                    info = _extract_event_info(event)
                    attractions = event.get("_embedded", {}).get("attractions", [])
                    info["tm_artists"] = [a.get("name", "") for a in attractions]
                    info["tm_event_name"] = event.get("name", "")
                    all_events.append(info)
                    new_count += 1

            page += 1
            time.sleep(0.25)

        date_range = f"{window_start.strftime('%m/%d')}–{window_end.strftime('%m/%d')}"
        print(f"   Window {window_num} ({date_range}): {len(all_events)} NYC events total")
        window_start = window_end

    print(f"   Done! {len(all_events)} NYC music events fetched.")
    return all_events


def match_events_to_artists(
    events: List[Dict], gaf: Dict[str, float]
) -> List[Dict]:
    """Match TM events against the give-a-fuck artist list."""
    # Build normalized lookup: norm_name → (original_name, score)
    lookup: Dict[str, Tuple[str, float]] = {}
    for name, score in gaf.items():
        lookup[normalize(name)] = (name, score)

    matches = []
    for event in events:
        # Try matching against TM attractions (structured artist names)
        matched = False
        for tm_artist in event.get("tm_artists", []):
            result = _try_match(tm_artist, lookup)
            if result:
                orig_name, score = result
                matches.append({
                    "artist": orig_name,
                    "score": score,
                    "date": event["date"],
                    "venue": event["venue"],
                    "url": event["url"],
                    "price": event["price"],
                    "tm_name": event.get("tm_event_name", ""),
                })
                matched = True
                break  # one match per event is enough

        # Fallback: try matching against event name itself
        if not matched:
            result = _try_match(event.get("tm_event_name", ""), lookup)
            if result:
                orig_name, score = result
                matches.append({
                    "artist": orig_name,
                    "score": score,
                    "date": event["date"],
                    "venue": event["venue"],
                    "url": event["url"],
                    "price": event["price"],
                    "tm_name": event.get("tm_event_name", ""),
                })

    return matches


def _try_match(
    name: str, lookup: Dict[str, Tuple[str, float]]
) -> Optional[Tuple[str, float]]:
    """Try to match a name against the normalized artist lookup."""
    if not name.strip():
        return None

    norm = normalize(name)

    # Exact normalized match
    if norm in lookup:
        return lookup[norm]

    # Try extracting core name (before " - ", " : ", etc.)
    for sep in [" - ", " – ", " — ", ": ", " | "]:
        if sep in name:
            core = normalize(name.split(sep)[0])
            if core in lookup:
                return lookup[core]
            break

    # Fuzzy match — but require higher threshold for short names
    # (short names like "Ella", "Holly", "Dave" cause false positives)
    best_match = None
    best_score = 0.0

    for norm_artist, (orig_name, gaf_score) in lookup.items():
        # Skip very short names for fuzzy matching — exact only
        min_len = min(len(norm), len(norm_artist))
        if min_len < 5:
            continue

        sim = SequenceMatcher(None, norm, norm_artist).ratio()
        # Require higher similarity for shorter names
        threshold = 0.90 if min_len < 8 else 0.85
        if sim > best_score and sim >= threshold:
            best_score = sim
            best_match = (orig_name, gaf_score)

    return best_match


# ─── Step 3: Output ──────────────────────────────────────────────────────────

def print_report(matches: List[Dict]):
    """Print the ranked concert report."""
    today = datetime.now()

    # Dedupe by artist + date + venue
    seen = set()
    unique = []
    for m in matches:
        key = (m["artist"], m["date"], m["venue"])
        if key not in seen:
            seen.add(key)
            unique.append(m)

    # Add days_away
    for m in unique:
        try:
            m["days_away"] = (datetime.strptime(m["date"], "%Y-%m-%d") - today).days
        except ValueError:
            m["days_away"] = -1

    # Sort: score desc, then date asc
    unique.sort(key=lambda m: (-m["score"], m["date"]))

    urgent = [m for m in unique if m["score"] >= 30 and 0 <= m["days_away"] <= 30]
    planned = [m for m in unique if m["score"] >= 30 and m["days_away"] > 30]

    print(f"\n{'=' * 100}")
    print("  YOUR UPCOMING CONCERTS — ranked by how much you give a fuck")
    print(f"{'=' * 100}")

    if not unique:
        print("\n  No matches found. Your favorite artists aren't playing NYC soon.")
        return

    # ACT NOW section
    if urgent:
        print(f"\n  ACT NOW — {len(urgent)} high-priority concerts in the next 30 days:")
        print(f"  {'-' * 96}")
        _print_table(urgent)

    # PLAN AHEAD section
    if planned:
        print(f"\n  PLAN AHEAD — {len(planned)} high-priority concerts 30+ days out:")
        print(f"  {'-' * 96}")
        _print_table(planned)

    # Full list
    remaining = [m for m in unique if m not in urgent and m not in planned]
    if remaining:
        print(f"\n  EVERYTHING ELSE ({len(remaining)} concerts):")
        print(f"  {'-' * 96}")
        _print_table(remaining)

    print(f"\n{'=' * 100}")
    print(f"  {len(unique)} concerts by artists you care about")
    print(f"{'=' * 100}")


def _print_table(items: List[Dict]):
    print(
        f"  {'Score':>5}  {'Artist':<25} {'Date':<12} {'Venue':<30} {'Days':>4}  Tickets"
    )
    print(
        f"  {'-' * 5}  {'-' * 25} {'-' * 12} {'-' * 30} {'-' * 4}  {'-' * 40}"
    )
    for m in items:
        score = str(int(m["score"]))
        artist = m["artist"][:25]
        date = m["date"]
        venue = m["venue"][:30]
        days = str(m["days_away"]) if m["days_away"] >= 0 else "?"
        url = m.get("url", "")
        print(f"  {score:>5}  {artist:<25} {date:<12} {venue:<30} {days:>4}  {url}")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    if not TM_API_KEY:
        print("TICKETMASTER_API_KEY not found. Check .env.local in putmeon/loopedin/")
        sys.exit(1)

    # Step 1: Build "give a fuck" list from Spotify
    saved_counts = get_saved_tracks_artists()
    top_scores = get_top_artists()
    gaf = build_give_a_fuck_list(saved_counts, top_scores)

    print(f"\n{len(gaf)} artists you give a fuck about (score >= {SCORE_THRESHOLD})")
    top_10 = sorted(gaf.items(), key=lambda x: -x[1])[:10]
    for name, score in top_10:
        print(f"   {int(score):>5}  {name}")
    if len(gaf) > 10:
        print(f"   ... and {len(gaf) - 10} more")

    # Step 2: Bulk-fetch all NYC events, then match against Spotify list
    print(f"\nFetching all NYC music events from Ticketmaster (through {END_DATE})...")
    events = fetch_all_nyc_events()

    print(f"\nMatching {len(events)} events against {len(gaf)} artists...")
    all_matches = match_events_to_artists(events, gaf)
    print(f"   {len(all_matches)} matches found!")

    # Step 3: Report
    print_report(all_matches)


if __name__ == "__main__":
    main()
