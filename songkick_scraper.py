"""
Songkick Festival Discovery Scraper.
Given artists you like, find festivals they've played, grab the other artists
on those lineups, and create a Spotify playlist to sample new music.

Usage:
    python3 songkick_scraper.py "Hot Since 82, Disclosure" "Festival Discovery"
    python3 songkick_scraper.py "Peggy Gou" --max-festivals 3
"""

import os
import sys
import re
import json
import time
import argparse
from dotenv import load_dotenv
from openai import OpenAI
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import main as spotify_main

load_dotenv()

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY1"))

SONGKICK_ROOT = "https://www.songkick.com"


def create_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument(
        "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    )
    chrome_options.page_load_strategy = "eager"
    driver = webdriver.Chrome(options=chrome_options)
    driver.set_page_load_timeout(15)
    driver.implicitly_wait(3)
    return driver


def llm_call(system_prompt, user_prompt):
    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
    )
    return response.choices[0].message.content.strip()


def search_artist(driver, artist_name):
    """Search Songkick for an artist and use LLM to pick the correct result."""
    query = artist_name.replace(" ", "+")
    url = f"{SONGKICK_ROOT}/search?query={query}&type=artists"
    print(f"   Searching Songkick for '{artist_name}'...")
    driver.get(url)
    time.sleep(2)

    # Results are <li class="artist"> inside a <ul>, with <a class="search-link">
    items = driver.find_elements(By.CSS_SELECTOR, "li.artist")

    if not items:
        print(f"   No Songkick results for '{artist_name}'")
        return None

    results_text = []
    result_links = []
    for i, item in enumerate(items[:10], 1):
        # Get the artist name from p.summary > a
        try:
            name_el = item.find_element(By.CSS_SELECTOR, "p.summary a.search-link")
            name = name_el.text.strip()
            link = name_el.get_attribute("href")
        except Exception:
            continue
        # Get event count text from .subject
        try:
            subject_text = item.find_element(By.CSS_SELECTOR, ".subject").text
            # Extract "N upcoming events" line
            event_info = [line.strip() for line in subject_text.split("\n") if "event" in line.lower()]
            event_str = event_info[0] if event_info else ""
        except Exception:
            event_str = ""

        display = f"{name} - {event_str}" if event_str else name
        results_text.append(f"{i}. {display}")
        result_links.append(link)

    if not results_text:
        print(f"   No parseable results for '{artist_name}'")
        return None

    # If only one result, just use it
    if len(results_text) == 1:
        idx = 0
    else:
        numbered_results = "\n".join(results_text)
        prompt = (
            f'Given these Songkick search results, which one is the artist "{artist_name}"?\n'
            f"Return ONLY the number.\n\n{numbered_results}"
        )
        answer = llm_call(
            "You pick the correct artist from search results. Return only a single number.",
            prompt,
        )
        try:
            idx = int(re.search(r"\d+", answer).group()) - 1
        except Exception:
            idx = 0
        if idx < 0 or idx >= len(result_links):
            idx = 0

    chosen_link = result_links[idx]
    print(f"   Selected: {results_text[idx]}")
    print(f"   URL: {chosen_link}")
    driver.get(chosen_link)
    time.sleep(2)
    return chosen_link


def get_past_events(driver):
    """From the artist page, navigate to gigography and scrape events."""
    current_url = driver.current_url.rstrip("/")
    gigography_url = current_url + "/gigography"
    driver.get(gigography_url)
    time.sleep(2)

    events = []
    pages_scraped = 0
    max_pages = 5

    while pages_scraped < max_pages:
        pages_scraped += 1
        # Events are <li title="..."> inside <ul class="event-listings">
        # Date headers are <li class="with-date"> — skip those
        event_elements = driver.find_elements(By.CSS_SELECTOR, "ul.event-listings li[title]")

        for el in event_elements:
            try:
                text = el.text.strip()
                if not text:
                    continue
                # Get the first link (event link, not venue link)
                links = el.find_elements(By.TAG_NAME, "a")
                link = links[0].get_attribute("href") if links else None
                events.append({"name": text.replace("\n", " | "), "url": link})
            except Exception:
                continue

        # Try next page
        try:
            next_link = driver.find_element(
                By.CSS_SELECTOR, "a.next_page, .pagination a[rel='next']"
            )
            next_link.click()
            time.sleep(2)
        except Exception:
            break

    print(f"   Found {len(events)} past events (across {pages_scraped} page(s))")
    return events


def identify_festivals(events):
    """Identify festivals from events — uses URL pattern + LLM fallback."""
    if not events:
        return []

    # Songkick festival URLs contain /festivals/ — use that as primary signal
    festivals = []
    ambiguous = []
    for i, e in enumerate(events):
        url = e.get("url") or ""
        if "/festivals/" in url:
            festivals.append(e)
        elif "festival" in e["name"].lower():
            ambiguous.append(e)

    # Add ambiguous ones that look like festivals by name
    festivals.extend(ambiguous)

    if not festivals:
        # Fallback: ask LLM to identify festivals from event names
        numbered = "\n".join(f"{i+1}. {e['name']}" for i, e in enumerate(events))
        prompt = (
            "Which of these events are music FESTIVALS (multi-day, multiple artists/stages)?\n"
            "Exclude regular club nights, single-venue shows, tours, and DJ sets at clubs.\n"
            "Return a JSON array of the event numbers that are festivals.\n"
            "If none are festivals, return an empty array [].\n\n"
            f"{numbered}"
        )
        answer = llm_call(
            "You identify music festivals from event lists. Return only valid JSON — a JSON array of integers.",
            prompt,
        )
        answer = re.sub(r"^```(?:json)?\s*\n?", "", answer)
        answer = re.sub(r"\n?```\s*$", "", answer)
        try:
            indices = json.loads(answer.strip())
        except json.JSONDecodeError:
            indices = [int(x) for x in re.findall(r"\d+", answer)]
        if isinstance(indices, list):
            for idx in indices:
                actual_idx = idx - 1
                if 0 <= actual_idx < len(events):
                    festivals.append(events[actual_idx])

    return festivals


def scrape_festival_lineup(driver, festival_url):
    """Navigate to a festival page and scrape lineup text."""
    if not festival_url:
        return ""

    # Navigate to the festival's lineup page if possible
    driver.get(festival_url)
    time.sleep(2)

    # Try clicking a "Line-up" or "Lineup" tab/link if present
    try:
        lineup_link = driver.find_element(
            By.XPATH,
            "//a[contains(translate(text(),'LINEUP','lineup'),'lineup') or "
            "contains(translate(text(),'LINE-UP','line-up'),'line-up')]",
        )
        lineup_link.click()
        time.sleep(2)
    except Exception:
        pass

    # Scrape the page text
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
    except Exception:
        body_text = ""

    return body_text


def extract_artists_from_lineup(text, source_name=""):
    """Use LLM to extract artist names from festival lineup page text."""
    if not text or len(text.strip()) < 20:
        return []

    # Truncate very long pages
    if len(text) > 8000:
        text = text[:8000]

    prompt = (
        "Extract the names of musical artists and performers from this festival lineup page.\n\n"
        "RULES:\n"
        "1. Include ONLY musicians, DJs, bands, and musical performers\n"
        "2. EXCLUDE venue names, sponsors, dates, locations, and non-artist text\n"
        "3. Clean up names: remove suffixes like '(Live)', '(DJ Set)', etc.\n"
        "4. Return ONLY a JSON array of artist name strings\n\n"
        f"Festival page text:\n{text}"
    )

    answer = llm_call(
        "You extract artist names from festival pages. Return only valid JSON — a JSON array of strings.",
        prompt,
    )

    answer = re.sub(r"^```(?:json)?\s*\n?", "", answer)
    answer = re.sub(r"\n?```\s*$", "", answer)

    try:
        artists = json.loads(answer.strip())
    except json.JSONDecodeError:
        print(f"   Failed to parse artist list from {source_name}")
        return []

    if not isinstance(artists, list):
        return []

    return [a.strip() for a in artists if isinstance(a, str) and a.strip()]


def run(artist_names, playlist_name="Festival Discovery", max_festivals=5):
    """
    Main orchestrator:
    - For each input artist: search Songkick -> past events -> identify festivals
    - Scrape top N festival lineups -> extract artists
    - Deduplicate, remove input artists
    - Call main.main() to create Spotify playlist
    """
    print("=" * 60)
    print("Songkick Festival Discovery")
    print("=" * 60)
    print(f"Input artists: {', '.join(artist_names)}")
    print(f"Playlist: {playlist_name}")
    print(f"Max festivals per artist: {max_festivals}")
    print()

    driver = create_driver()
    all_discovered = []
    input_names_lower = {name.strip().lower() for name in artist_names}

    try:
        for artist_name in artist_names:
            print(f"\n--- {artist_name} ---")

            # Step 1: Search for artist on Songkick
            artist_link = search_artist(driver, artist_name)
            if not artist_link:
                print(f"   Skipping '{artist_name}' — not found on Songkick")
                continue

            # Step 2: Get past events
            events = get_past_events(driver)
            if not events:
                print(f"   No past events found for '{artist_name}'")
                continue

            # Step 3: Identify which events are festivals
            festivals = identify_festivals(events)
            print(f"   Identified {len(festivals)} festival(s)")

            if not festivals:
                print(f"   No festivals found for '{artist_name}'")
                continue

            # Step 4: Scrape lineups from top N festivals
            festivals_to_scrape = festivals[:max_festivals]
            for i, fest in enumerate(festivals_to_scrape, 1):
                fest_name = fest["name"][:80]
                print(f"   [{i}/{len(festivals_to_scrape)}] Scraping: {fest_name}")

                lineup_text = scrape_festival_lineup(driver, fest["url"])
                if not lineup_text:
                    print(f"      No lineup text found")
                    continue

                artists = extract_artists_from_lineup(lineup_text, fest_name)
                print(f"      Found {len(artists)} artist(s)")
                all_discovered.extend(artists)

    finally:
        driver.quit()

    # Deduplicate and remove input artists
    seen = set()
    unique_artists = []
    for artist in all_discovered:
        normalized = artist.strip().lower()
        if normalized and normalized not in seen and normalized not in input_names_lower:
            seen.add(normalized)
            unique_artists.append(artist)

    print(f"\n{'=' * 60}")
    print(f"Discovered {len(unique_artists)} unique new artists")
    if unique_artists:
        print("Sample: " + ", ".join(unique_artists[:15]))
        if len(unique_artists) > 15:
            print(f"   ... and {len(unique_artists) - 15} more")
    print()

    if not unique_artists:
        print("No new artists discovered. Nothing to add to playlist.")
        return

    # Step 5: Create Spotify playlist via main.py
    print("Creating Spotify playlist...")
    spotify_main.main(unique_artists, playlist_name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Discover new artists from festivals your favorite artists have played"
    )
    parser.add_argument(
        "artists",
        help='Comma-separated artist names, e.g. "Hot Since 82, Disclosure"',
    )
    parser.add_argument(
        "playlist_name",
        nargs="?",
        default="Festival Discovery",
        help="Name for the Spotify playlist",
    )
    parser.add_argument(
        "--max-festivals",
        type=int,
        default=5,
        help="Max festivals to scrape per artist (default: 5)",
    )

    args = parser.parse_args()
    artist_list = [name.strip() for name in args.artists.split(",") if name.strip()]

    if not artist_list:
        print("No artists provided.")
        sys.exit(1)

    run(artist_list, args.playlist_name, args.max_festivals)
