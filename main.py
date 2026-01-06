# import os
# import sys
# import spotipy
# from spotipy.oauth2 import SpotifyOAuth
# from dotenv import load_dotenv
# from artist_chooser import artist_chooser

# # Load environment variables from .env file
# load_dotenv()

# SPOTIPY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
# SPOTIPY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
# SPOTIPY_REDIRECT_URI = os.getenv(
#     "SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback"
# )
# SCOPE = "playlist-modify-public playlist-modify-private"

# sp = spotipy.Spotify(
#     auth_manager=SpotifyOAuth(
#         scope=SCOPE,
#         client_id=SPOTIPY_CLIENT_ID,
#         client_secret=SPOTIPY_CLIENT_SECRET,
#         redirect_uri=SPOTIPY_REDIRECT_URI,
#     )
# )


# # def get_artist_id(artist_name):
# #     results = sp.search(q=f"artist:{artist_name}", type="artist", limit=1)
# #     items = results["artists"]["items"]
# #     return items[0]["id"] if items else None

# def get_artist_id(artist_name):
#     try:
#         results = sp.search(q=artist_name, type="artist", limit=5)
#         candidates = results["artists"]["items"]
#         if not candidates:
#             return None
#         chosen = artist_chooser(artist_name, candidates)
#         return chosen["id"] if chosen else None
#     except Exception as e:
#         print(f"⚠️ Failed to search or choose artist for '{artist_name}': {e}")
#         return None

# def get_top_tracks(artist_id, limit=5):
#     official_top = sp.artist_top_tracks(artist_id, country="US")["tracks"]
#     return [track["id"] for track in official_top[:limit]]


# def get_recent_releases(artist_id, limit=5):
#     albums = sp.artist_albums(artist_id, album_type="single,album", limit=20)
#     all_tracks = []
#     seen_albums = set()
#     for album in albums["items"]:
#         if album["name"] in seen_albums:
#             continue
#         seen_albums.add(album["name"])
#         tracks = sp.album_tracks(album["id"])["items"]
#         all_tracks.extend(tracks)
#         if len(all_tracks) >= limit:
#             break
#     return [track["id"] for track in all_tracks[:limit]]


# def get_most_popular_album_tracks(artist_id, min_tracks=2):
#     albums = sp.artist_albums(artist_id, album_type="album", limit=50)
#     album_stats = []
#     seen_album_names = set()

#     print(f"🗂️ Found {len(albums['items'])} albums for artist")

#     for album in albums["items"]:
#         album_name = album["name"].strip().lower()
#         if album_name in seen_album_names:
#             continue
#         seen_album_names.add(album_name)

#         # Skip remix/alternate albums
#         if any(keyword in album_name for keyword in ["remix", "edit", "rework", "version", "remastered"]):
#             print(f"🚫 Skipping remix/alternate album '{album_name}'")
#             continue

#         try:
#             album_full = sp.album(album["id"])
#             track_items = sp.album_tracks(album["id"])["items"]
#         except Exception as e:
#             print(f"⚠️ Error loading album '{album_name}': {e}")
#             continue

#         if len(track_items) < min_tracks:
#             print(f"🚫 Skipping album '{album_name}' (only {len(track_items)} tracks)")
#             continue

#         track_ids = [t["id"] for t in track_items]
#         try:
#             track_pops = []
#             for tid in track_ids:
#                 try:
#                     pop = sp.track(tid)["popularity"]
#                     track_pops.append(pop)
#                 except Exception as e:
#                     print(f"⚠️ Could not get popularity for track ID {tid}: {e}")
#             if not track_pops:
#                 continue
#             avg_popularity = sum(track_pops) / len(track_pops)
#         except Exception as e:
#             print(f"⚠️ Failed popularity calc for album '{album_name}': {e}")
#             continue

#         album_stats.append((avg_popularity, album_full, track_items))

#     if not album_stats:
#         print("❌ No eligible full-length albums found after filtering. Retrying with relaxed rules...")
#         return get_most_popular_album_tracks_relaxed(artist_id, min_tracks)

#     best_album_stat = max(album_stats, key=lambda x: x[0])
#     avg_pop, best_album, track_items = best_album_stat

#     print(f"\n🎧 Most popular album: **{best_album['name']}**")
#     print(
#         f"📅 Released: {best_album['release_date']} | 🧮 {len(track_items)} tracks | 🔥 Avg popularity: {avg_pop:.1f}"
#     )

#     track_ids = []
#     for t in track_items:
#         print(f"   - {t['track_number']}. {t['name']}")
#         track_ids.append(t["id"])

#     return track_ids, best_album["name"]


# def get_or_create_playlist(user_id, playlist_name="Escuchar"):
#     normalized_name = playlist_name.strip().lower()
#     playlists = []
#     limit = 50
#     offset = 0

#     # Fetch all user playlists
#     while True:
#         page = sp.current_user_playlists(limit=limit, offset=offset)
#         playlists.extend(page["items"])
#         if len(page["items"]) < limit:
#             break
#         offset += limit

#     # Check for existing playlist with exact name (case-insensitive)
#     for playlist in playlists:
#         if playlist["name"].strip().lower() == normalized_name:
#             print(f"ℹ️ Found existing playlist: {playlist['name']}")
#             return playlist["id"], playlist["external_urls"]["spotify"]

#     # If not found, create one
#     new_playlist = sp.user_playlist_create(
#         user=user_id, name=playlist_name, public=False
#     )
#     print(f"🆕 Created new playlist: {playlist_name}")
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


# def main(artist_csv, playlist_name="Escuchar"):
#     artist_names = [name.strip() for name in artist_csv.split(",") if name.strip()]
#     user_id = sp.current_user()["id"]
#     playlist_id, playlist_url = get_or_create_playlist(
#         user_id, playlist_name=playlist_name
#     )

#     for artist_name in artist_names:
#         print(f"\n🔍 Processing artist: {artist_name}")
#         artist_id = get_artist_id(artist_name)
#         if not artist_id:
#             print(f"❌ Artist '{artist_name}' not found.")
#             continue

#         top_tracks = get_top_tracks(artist_id, limit=7)
#         recent_tracks = get_recent_releases(artist_id, limit=5)
#         album_tracks, album_name = get_most_popular_album_tracks(artist_id)

#         all_tracks = list(dict.fromkeys(top_tracks + recent_tracks + album_tracks))

#         if not all_tracks:
#             print(f"⚠️ No tracks found for {artist_name}")
#             continue

#         add_tracks_to_playlist(playlist_id, all_tracks)

#         print(
#             f"🎧 Added {len(all_tracks)} total tracks from '{artist_name}' to your '{playlist_name}' playlist."
#         )
#         if album_name:
#             print(f"   ↪ Most popular album: *{album_name}*")

#     print(f"\n🔗 Final playlist link: {playlist_url}")


# if __name__ == "__main__":
#     if len(sys.argv) < 2:
#         print("Usage: python3 main.py 'Artist 1, Artist 2' [playlist_name]")
#         print("Example: python3 main.py 'Daft Punk, The Weeknd' 'My Awesome Mix'")
#         sys.exit(1)

#     artist_input = sys.argv[1]
#     playlist_name = sys.argv[2] if len(sys.argv) > 2 else "Escuchar"
    
#     main(artist_input, playlist_name)

import os
import sys
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


def artist_chooser(search_term, candidates):
    """
    Simple artist chooser - returns the most popular artist from candidates.
    You can replace this with your own logic if you have a separate artist_chooser module.
    """
    if not candidates:
        return None
    
    # Sort by popularity and return the most popular
    sorted_candidates = sorted(candidates, key=lambda x: x.get('popularity', 0), reverse=True)
    
    print(f"🎤 Found {len(candidates)} artists matching '{search_term}':")
    for i, artist in enumerate(sorted_candidates[:3]):  # Show top 3
        print(f"   {i+1}. {artist['name']} (popularity: {artist.get('popularity', 0)})")
    
    # Return the most popular one
    chosen = sorted_candidates[0]
    print(f"   ✓ Selected: {chosen['name']}")
    return chosen


def get_artist_id(artist_name):
    try:
        results = sp.search(q=artist_name, type="artist", limit=5)
        candidates = results["artists"]["items"]
        if not candidates:
            return None
        chosen = artist_chooser(artist_name, candidates)
        return chosen["id"] if chosen else None
    except Exception as e:
        print(f"⚠️ Failed to search or choose artist for '{artist_name}': {e}")
        return None


def get_top_tracks(artist_id, limit=5):
    try:
        official_top = sp.artist_top_tracks(artist_id, country="US")["tracks"]
        return [track["id"] for track in official_top[:limit] if track.get("id")]
    except Exception as e:
        print(f"⚠️ Failed to get top tracks: {e}")
        return []


def get_recent_releases(artist_id, limit=5):
    try:
        albums = sp.artist_albums(artist_id, album_type="single,album", limit=20)
        all_tracks = []
        seen_albums = set()
        
        for album in albums["items"]:
            if album["name"] in seen_albums:
                continue
            seen_albums.add(album["name"])
            
            try:
                tracks = sp.album_tracks(album["id"])["items"]
                all_tracks.extend(tracks)
                if len(all_tracks) >= limit:
                    break
            except Exception as e:
                print(f"⚠️ Failed to get tracks for album {album['name']}: {e}")
                continue
                
        return [track["id"] for track in all_tracks[:limit] if track.get("id")]
    except Exception as e:
        print(f"⚠️ Failed to get recent releases: {e}")
        return []


def get_most_popular_album_tracks_relaxed(artist_id, min_tracks=1):
    """
    Fallback function with relaxed filtering rules.
    """
    try:
        albums = sp.artist_albums(artist_id, album_type="album,compilation", limit=20)
        
        if not albums["items"]:
            return [], None
        
        # Just get tracks from the first available album
        for album in albums["items"]:
            try:
                track_items = sp.album_tracks(album["id"])["items"]
                if len(track_items) >= min_tracks:
                    track_ids = [t["id"] for t in track_items if t.get("id")]
                    print(f"🎧 Using album: {album['name']} (relaxed rules)")
                    return track_ids, album["name"]
            except Exception as e:
                continue
                
        return [], None
    except Exception as e:
        print(f"⚠️ Failed in relaxed album search: {e}")
        return [], None


def get_most_popular_album_tracks(artist_id, min_tracks=2):
    try:
        albums = sp.artist_albums(artist_id, album_type="album", limit=50)
        album_stats = []
        seen_album_names = set()

        print(f"🗂️ Found {len(albums['items'])} albums for artist")

        for album in albums["items"]:
            album_name = album["name"].strip().lower()
            if album_name in seen_album_names:
                continue
            seen_album_names.add(album_name)

            # Skip remix/alternate albums
            if any(keyword in album_name for keyword in ["remix", "edit", "rework", "version", "remastered"]):
                print(f"🚫 Skipping remix/alternate album '{album['name']}'")
                continue

            try:
                album_full = sp.album(album["id"])
                track_items = sp.album_tracks(album["id"])["items"]
            except Exception as e:
                print(f"⚠️ Error loading album '{album['name']}': {e}")
                continue

            if len(track_items) < min_tracks:
                print(f"🚫 Skipping album '{album['name']}' (only {len(track_items)} tracks)")
                continue

            track_ids = [t["id"] for t in track_items if t.get("id")]
            
            try:
                track_pops = []
                for tid in track_ids:
                    try:
                        track_data = sp.track(tid)
                        if track_data and "popularity" in track_data:
                            track_pops.append(track_data["popularity"])
                    except Exception as e:
                        print(f"⚠️ Could not get popularity for track ID {tid}: {e}")
                
                if not track_pops:
                    continue
                    
                avg_popularity = sum(track_pops) / len(track_pops)
            except Exception as e:
                print(f"⚠️ Failed popularity calc for album '{album['name']}': {e}")
                continue

            album_stats.append((avg_popularity, album_full, track_items))

        if not album_stats:
            print("❌ No eligible full-length albums found after filtering. Retrying with relaxed rules...")
            return get_most_popular_album_tracks_relaxed(artist_id, min_tracks)

        best_album_stat = max(album_stats, key=lambda x: x[0])
        avg_pop, best_album, track_items = best_album_stat

        print(f"\n🎧 Most popular album: **{best_album['name']}**")
        print(
            f"📅 Released: {best_album['release_date']} | 🧮 {len(track_items)} tracks | 🔥 Avg popularity: {avg_pop:.1f}"
        )

        track_ids = []
        for t in track_items:
            print(f"   - {t['track_number']}. {t['name']}")
            if t.get("id"):
                track_ids.append(t["id"])

        return track_ids, best_album["name"]
    
    except Exception as e:
        print(f"⚠️ Failed to get most popular album tracks: {e}")
        return get_most_popular_album_tracks_relaxed(artist_id, min_tracks)


def get_or_create_playlist(user_id, playlist_name="Escuchar1"):
    try:
        normalized_name = playlist_name.strip().lower()
        playlists = []
        limit = 50
        offset = 0

        # Fetch all user playlists
        while True:
            page = sp.current_user_playlists(limit=limit, offset=offset)
            playlists.extend(page["items"])
            if len(page["items"]) < limit:
                break
            offset += limit

        # Check for existing playlist with exact name (case-insensitive)
        for playlist in playlists:
            if playlist and playlist.get("name", "").strip().lower() == normalized_name:
                print(f"ℹ️ Found existing playlist: {playlist['name']}")
                return playlist["id"], playlist["external_urls"]["spotify"]

        # If not found, create one
        new_playlist = sp.user_playlist_create(
            user=user_id, name=playlist_name, public=False
        )
        print(f"🆕 Created new playlist: {playlist_name}")
        return new_playlist["id"], new_playlist["external_urls"]["spotify"]
    
    except Exception as e:
        print(f"⚠️ Failed to get or create playlist: {e}")
        raise


def add_tracks_to_playlist(playlist_id, new_track_ids):
    try:
        existing_ids = set()
        limit = 100
        offset = 0

        while True:
            response = sp.playlist_tracks(
                playlist_id, fields="items.track.id,total", limit=limit, offset=offset
            )
            items = response.get("items", [])
            if not items:
                break
            for item in items:
                if item and item.get("track") and item["track"].get("id"):
                    existing_ids.add(item["track"]["id"])
            offset += limit
            if len(items) < limit:
                break

        # Filter out None values and duplicates
        valid_new_ids = [tid for tid in new_track_ids if tid and tid not in existing_ids]

        if valid_new_ids:
            # Spotify API limits to 100 tracks per request
            for i in range(0, len(valid_new_ids), 100):
                batch = valid_new_ids[i:i+100]
                sp.playlist_add_items(playlist_id, batch)
            print(f"✅ Added {len(valid_new_ids)} new tracks to playlist.")
        else:
            print("ℹ️ No new tracks to add — all are already in the playlist.")
    
    except Exception as e:
        print(f"⚠️ Failed to add tracks to playlist: {e}")


def main(artist_csv, playlist_name="Escuchar"):
    try:
        artist_names = [name.strip() for name in artist_csv.split(",") if name.strip()]
        
        if not artist_names:
            print("❌ No artist names provided.")
            return
        
        user_id = sp.current_user()["id"]
        playlist_id, playlist_url = get_or_create_playlist(
            user_id, playlist_name=playlist_name
        )

        total_added = 0
        for artist_name in artist_names:
            print(f"\n🔍 Processing artist: {artist_name}")
            artist_id = get_artist_id(artist_name)
            if not artist_id:
                print(f"❌ Artist '{artist_name}' not found.")
                continue

            top_tracks = get_top_tracks(artist_id, limit=7)
            recent_tracks = get_recent_releases(artist_id, limit=5)
            album_tracks, album_name = get_most_popular_album_tracks(artist_id)

            # Combine all tracks and remove duplicates while preserving order
            all_tracks = list(dict.fromkeys(top_tracks + recent_tracks + album_tracks))
            
            # Filter out any None values
            all_tracks = [t for t in all_tracks if t]

            if not all_tracks:
                print(f"⚠️ No tracks found for {artist_name}")
                continue

            add_tracks_to_playlist(playlist_id, all_tracks)
            total_added += len(all_tracks)

            print(
                f"🎧 Added up to {len(all_tracks)} tracks from '{artist_name}' to your '{playlist_name}' playlist."
            )
            if album_name:
                print(f"   ↪ Most popular album: *{album_name}*")

        print(f"\n✨ Finished! Added tracks from {len(artist_names)} artist(s)")
        print(f"🔗 Final playlist link: {playlist_url}")
        
    except Exception as e:
        print(f"❌ Critical error in main: {e}")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 main.py 'Artist 1, Artist 2' [playlist_name]")
        print("Example: python3 main.py 'Daft Punk, The Weeknd' 'My Awesome Mix'")
        sys.exit(1)

    artist_input = sys.argv[1]
    playlist_name = sys.argv[2] if len(sys.argv) > 2 else "Escuchar"
    
    main(artist_input, playlist_name)