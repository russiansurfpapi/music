import os
import re
import requests
import logging
from dotenv import load_dotenv
from bs4 import BeautifulSoup
import spotipy
from spotipy import SpotifyOAuth

# Load environment variables
load_dotenv()
BRIGHTDATA_API_KEY = os.getenv("BRIGHT_DATA_API_KEY")
BRIGHTDATA_ZONE = os.getenv("BRIGHTDATA_ZONE")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI")
SPOTIFY_USERNAME = os.getenv("SPOTIFY_USERNAME")

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Spotify setup
scope = "playlist-modify-public"
spotify = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI,
    scope=scope,
    username=SPOTIFY_USERNAME,
    open_browser=False
))

# Fetch HTML with rendering enabled
def fetch_html(url):
    logger.info(f"🌐 Fetching rendered HTML via Bright Data for {url}")
    response = requests.post(
        "https://api.brightdata.com/request",
        headers={"Authorization": f"Bearer {BRIGHTDATA_API_KEY}"},
        json={
            "zone": BRIGHTDATA_ZONE,
            "url": url,
            "format": "raw",
            "render": True  # ✅ important fix
        }
    )
    if response.status_code == 200:
        html = response.text
        with open("brightdata_tracklist.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("✅ HTML saved to brightdata_tracklist.html")
        return html
    else:
        logger.error(f"❌ Bright Data fetch failed: {response.status_code}\n{response.text[:300]}")
        return None

# Extract tracks in the format "1. Artist – Title"
def extract_tracks(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text()
    matches = re.findall(r'\d+\.\s+(.*?)\s+–\s+(.*)', text)
    tracks = [f"{artist.strip()} - {title.strip()}" for artist, title in matches]
    return list(set(tracks))  # Deduplicate

# Search for tracks on Spotify
def find_spotify_uris(track_names):
    uris = []
    for track in track_names:
        result = spotify.search(track, type="track", limit=1)
        items = result["tracks"]["items"]
        if items:
            uris.append(items[0]["uri"])
            print(f"✅ Found: {track}")
        else:
            print(f"❌ Not found: {track}")
    return uris

# Create a playlist and add tracks
def create_playlist(playlist_name, uris):
    playlist = spotify.user_playlist_create(SPOTIFY_USERNAME, playlist_name, public=True)
    spotify.playlist_add_items(playlist["id"], uris)
    print(f"🎧 Playlist created: {playlist['external_urls']['spotify']}")

# Orchestrator
def main(url, playlist_name="Escuchar"):
    html = fetch_html(url)
    if not html:
        return

    logger.info("🔍 Extracting tracks...")
    tracks = extract_tracks(html)
    print(f"✅ Extracted {len(tracks)} tracks.")
    if not tracks:
        return

    uris = find_spotify_uris(tracks)
    if uris:
        create_playlist(playlist_name, uris)
    else:
        print("❌ No tracks found on Spotify.")

# CLI
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python3 brightdata_to_spotify.py <1001tracklists_url>")
        sys.exit(1)

    url = sys.argv[1]
    main(url)
