"""Spotify auth — single premium account.

Separate cache file per scope so the tracklist scraper's auth isn't disturbed.
Plain requests.Session() to prevent spotipy's retry adapter from hammering 429s.
"""

import os

import requests
import spotipy
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

load_dotenv()

LIBRARY_SCOPE = " ".join([
    "user-library-read",
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-private",
    "playlist-modify-public",
    "user-read-recently-played",
    "user-top-read",
])

_HERE = os.path.dirname(os.path.abspath(__file__))


def get_spotify(scope: str = LIBRARY_SCOPE, cache_name: str = ".cache-library") -> spotipy.Spotify:
    session = requests.Session()
    return spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            scope=scope,
            client_id=os.getenv("SPOTIPY_CLIENT_ID"),
            client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
            redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
            cache_path=os.path.join(_HERE, cache_name),
            open_browser=True,
        ),
        requests_session=session,
        requests_timeout=30,
        retries=0,
    )


if __name__ == "__main__":
    sp = get_spotify()
    me = sp.current_user()
    print(f"Authed as: {me['display_name']} ({me['id']}) — {me.get('product', '?')}")
