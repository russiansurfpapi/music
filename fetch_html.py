import os
import re
import sys
import logging
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth

# Load environment variables from .env file
load_dotenv()

SPOTIPY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
SPOTIPY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
SPOTIPY_REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
BRIGHTDATA_API_KEY = os.getenv("BRIGHT_DATA_API_KEY")
BRIGHTDATA_ZONE = os.getenv("BRIGHTDATA_ZONE")
SCOPE = "playlist-modify-public playlist-modify-private"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    scope=SCOPE,
    client_id=SPOTIPY_CLIENT_ID,
    client_secret=SPOTIPY_CLIENT_SECRET,
    redirect_uri=SPOTIPY_REDIRECT_URI,
))

def fetch_html(url):
    logger.info(f"🌐 Fetching rendered HTML via Bright Data for {url}")
    response = requests.post(
        "https://api.brightdata.com/request",
        headers={"Authorization": f"Bearer {BRIGHTDATA_API_KEY}"},
        json={"zone": BRIGHTDATA_ZONE, "url": url, "format": "raw", "render": True}
    )
    if response.status_code == 200:
        html = response.text
        with open("brightdata_tracklist.html", "w", encoding="utf-8") as f:
            f.write(html)
        logger.info("✅ HTML saved to brightdata_tracklist.html")
        return html
    else:
        logger.error(f"❌ Bright Data fetch failed: {response.status_code}\n{response.text[:300]}")
        return None

# def extract_all_tracks(html):
#     soup = BeautifulSoup(html, "html.parser")
#     tracks = []

#     # Method 1: parse the <table class="default tracklist">
#     table = soup.find("table", class_="default tracklist")
#     if table:
#         for row in table.find_all("tr"):
#             artist = row.find("span", class_="trackFormatArtist")
#             title = row.find("span", class_="trackFormatTitle")
#             if artist and title:
#                 track = f"{artist.text.strip()} - {title.text.strip()}"
#                 tracks.append(track)

#     # Method 2: fallback - parse any <span id="tr_xxx">
#     for span in soup.find_all("span", id=re.compile(r"^tr_\d+$")):
#         if " - " in span.text:
#             tracks.append(span.text.strip())

#     return list(set(tracks))  # Deduplicate

# def find_spotify_track_ids(tracks):
#     track_ids = []
#     for track in tracks:
#         result = sp.search(track, type="track", limit=1)
#         items = result.get("tracks", {}).get("items", [])
#         if items:
#             tid = items[0]["id"]
#             track_ids.append(tid)
#             logger.info(f"✅ Found: {track}")
#         else:
#             logger.warning(f"❌ Not found: {track}")
#     return track_ids

# def get_or_create_playlist(user_id, playlist_name="Escuchar"):
#     normalized_name = playlist_name.strip().lower()
#     playlists = []
#     limit = 50
#     offset = 0

#     while True:
#         page = sp.current_user_playlists(limit=limit, offset=offset)
#         playlists.extend(page["items"])
#         if len(page["items"]) < limit:
#             break
#         offset += limit

#     for playlist in playlists:
#         if playlist["name"].strip().lower() == normalized_name:
#             print(f"ℹ️ Found existing playlist: {playlist['name']}")
#             return playlist["id"], playlist["external_urls"]["spotify"]

#     new_playlist = sp.user_playlist_create(user=user_id, name=playlist_name, public=False)
#     print(f"\U0001f195 Created new playlist: {playlist_name}")
#     return new_playlist["id"], new_playlist["external_urls"]["spotify"]

# def add_tracks_to_playlist(playlist_id, new_track_ids):
#     existing_ids = set()
#     limit = 100
#     offset = 0

#     while True:
#         response = sp.playlist_tracks(
#             playlist_id, fields="items.track.id,total", limit=limit, offset=offset
#         )
#         items = response["items"]
#         if not items:
#             break
#         for item in items:
#             if item["track"] and item["track"]["id"]:
#                 existing_ids.add(item["track"]["id"])
#         offset += limit
#         if len(items) < limit:
#             break

#     unique_new_ids = [tid for tid in new_track_ids if tid not in existing_ids]

#     if unique_new_ids:
#         sp.playlist_add_items(playlist_id, unique_new_ids)
#         print(f"✅ Added {len(unique_new_ids)} new tracks to playlist.")
#     else:
#         print("ℹ️ No new tracks to add — all are already in the playlist.")

def main(url):
    html = fetch_html(url)
    if not html:
        return

    # print("\n🔍 Extracting tracks from page...")
    # tracks = extract_all_tracks(html)
    # print(f"✅ Extracted {len(tracks)} track names")

    # if not tracks:
    #     print("❌ No tracks extracted")
    #     return

    # track_ids = find_spotify_track_ids(tracks)
    # if not track_ids:
    #     print("❌ No tracks found on Spotify")
    #     return

    # user_id = sp.current_user()["id"]
    # playlist_id, playlist_url = get_or_create_playlist(user_id, playlist_name="Escuchar")
    # add_tracks_to_playlist(playlist_id, track_ids)

    # print(f"\n🔗 Final playlist: {playlist_url}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 tracklist_to_spotify.py <1001tracklists_url>")
        sys.exit(1)
    main(sys.argv[1])
