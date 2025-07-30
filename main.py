import os
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

SPOTIPY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
SPOTIPY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
SPOTIPY_REDIRECT_URI = os.getenv(
    "SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback"
)

SCOPE = "playlist-modify-public playlist-modify-private"

sp = spotipy.Spotify(
    auth_manager=SpotifyOAuth(
        scope=SCOPE,
        client_id=SPOTIPY_CLIENT_ID,
        client_secret=SPOTIPY_CLIENT_SECRET,
        redirect_uri=SPOTIPY_REDIRECT_URI,
    )
)


def get_artist_id(artist_name):
    results = sp.search(q=f"artist:{artist_name}", type="artist", limit=1)
    items = results["artists"]["items"]
    return items[0]["id"] if items else None


def get_top_tracks(artist_id, limit=10):
    # Get Spotify's official "top tracks"
    official_top = sp.artist_top_tracks(artist_id, country="US")["tracks"]
    official_ids = [track["id"] for track in official_top]

    # Get all tracks across all albums and singles
    results = sp.artist_albums(artist_id, album_type="album,single", limit=50)
    track_scores = {}
    for album in results["items"]:
        album_tracks = sp.album_tracks(album["id"])["items"]
        for track in album_tracks:
            track_data = sp.track(track["id"])
            track_scores[track["id"]] = track_data["popularity"]

    # Sort by popularity and merge with official_top
    sorted_by_pop = sorted(track_scores.items(), key=lambda x: x[1], reverse=True)
    additional_ids = [tid for tid, _ in sorted_by_pop if tid not in official_ids]

    # Combine
    final_track_ids = official_ids + additional_ids
    return final_track_ids[:limit]


def get_most_popular_album_tracks(artist_id, min_tracks=3):
    # Get both albums and singles (some 'singles' are EPs or real albums mislabeled)
    albums = sp.artist_albums(artist_id, album_type="album,single", limit=50)
    album_stats = []
    seen_album_names = set()

    for album in albums["items"]:
        album_name = album["name"].strip().lower()
        if album_name in seen_album_names:
            continue
        seen_album_names.add(album_name)

        album_full = sp.album(album["id"])
        track_items = sp.album_tracks(album["id"])["items"]

        if len(track_items) < min_tracks:
            continue  # Skip singles/EPs with too few tracks

        track_ids = [t["id"] for t in track_items]
        try:
            track_pops = [sp.track(tid)["popularity"] for tid in track_ids]
        except:
            continue

        avg_popularity = sum(track_pops) / len(track_pops)
        album_stats.append((avg_popularity, album_full, track_items))

    if not album_stats:
        print("❌ No multi-track releases found.")
        return [], None

    # Select album with highest average track popularity
    best_album_stat = max(album_stats, key=lambda x: x[0])
    avg_pop, best_album, track_items = best_album_stat

    print(f"\n🎧 Most popular album-like release: **{best_album['name']}**")
    print(
        f"📅 Released: {best_album['release_date']} | 🧮 {len(track_items)} tracks | 🔥 Avg popularity: {avg_pop:.1f}"
    )

    track_ids = []
    for t in track_items:
        print(f"   - {t['track_number']}. {t['name']}")
        track_ids.append(t["id"])

    return track_ids, best_album["name"]


def get_recent_releases(artist_id, limit=5):
    albums = sp.artist_albums(artist_id, album_type="single,album", limit=20)
    all_tracks = []
    seen_albums = set()
    for album in albums["items"]:
        if album["name"] in seen_albums:
            continue
        seen_albums.add(album["name"])
        tracks = sp.album_tracks(album["id"])["items"]
        all_tracks.extend(tracks)
        if len(all_tracks) >= limit:
            break
    return [track["id"] for track in all_tracks[:limit]]


def get_or_create_playlist(user_id, playlist_name="ToListen"):
    playlists = sp.current_user_playlists(limit=50)
    for playlist in playlists["items"]:
        if playlist["name"].lower() == playlist_name.lower():
            return playlist["id"], playlist["external_urls"]["spotify"]
    new_playlist = sp.user_playlist_create(
        user=user_id, name=playlist_name, public=False
    )
    return new_playlist["id"], new_playlist["external_urls"]["spotify"]


def add_tracks_to_playlist(playlist_id, track_ids):
    sp.playlist_add_items(playlist_id, track_ids)


def main(artist_name):
    artist_id = get_artist_id(artist_name)
    if not artist_id:
        print("Artist not found.")
        return

    user_id = sp.current_user()["id"]
    playlist_id, playlist_url = get_or_create_playlist(user_id)

    top_tracks = get_top_tracks(artist_id)
    album_tracks, album_name = get_most_popular_album_tracks(artist_id)
    recent_tracks = get_recent_releases(artist_id)

    all_tracks = list(set(top_tracks + album_tracks + recent_tracks))
    add_tracks_to_playlist(playlist_id, all_tracks)

    print(
        f"✅ Added {len(all_tracks)} tracks from '{artist_name}' to your 'ToListen' playlist."
    )
    if album_name:
        print(f"🎧 Most popular album: *{album_name}*")
    print(f"🔗 Playlist link: {playlist_url}")


if __name__ == "__main__":
    artist = input("Enter artist name: ")
    main(artist)
