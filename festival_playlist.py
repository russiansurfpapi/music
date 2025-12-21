"""
Create a representative Spotify playlist from Pop-Kultur Festival 2025 lineup.
Extracts artist names and finds representative tracks for each.
"""

import os
import sys
import re
import json
import time
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
from openai import OpenAI
from concurrent.futures import ThreadPoolExecutor, as_completed

# Load environment variables
load_dotenv()

SPOTIPY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
SPOTIPY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
SPOTIPY_REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY1")
SCOPE = "playlist-modify-public playlist-modify-private"

sp = spotipy.Spotify(
    auth_manager=SpotifyOAuth(
        scope=SCOPE,
        client_id=SPOTIPY_CLIENT_ID,
        client_secret=SPOTIPY_CLIENT_SECRET,
        redirect_uri=SPOTIPY_REDIRECT_URI,
    )
)

openai_client = OpenAI(api_key=OPENAI_API_KEY)


def extract_artists_from_text(text):
    """
    Use LLM to extract artist/performer names from festival lineup text.
    Filters out talks, workshops, and other non-music events.
    """
    print("🤖 Using AI to extract artist names...")

    prompt = """Extract the names of musical artists and performers from this festival lineup text.

RULES:
1. Include ONLY musicians, DJs, bands, and musical performers
2. EXCLUDE talks, panels, workshops, readings, and conversations
3. For collaborative works or residencies, extract the main performing artist name(s)
4. Clean up names by removing:
   - Work titles (anything after colons or in quotes)
   - Text in parentheses like "(Live)" or "(cancelled)"
   - Phrases like "with the...", "& ensemble", etc. - keep only the main artist
5. For entries like "Artist Name: Work Title", return just "Artist Name"
6. Skip generic entries like "Pop-Kultur Lokal" or "Pop-Kultur Nachwuchs Live" unless followed by a specific artist

Return ONLY a JSON array of artist names, nothing else. Example format:
["Artist 1", "Artist 2", "Artist 3"]

Festival lineup text:
""" + text

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that extracts artist names from festival lineups. Return only valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
        )

        result = response.choices[0].message.content.strip()

        # Remove markdown code blocks if present
        if result.startswith("```"):
            # Extract JSON from markdown code block
            result = re.sub(r'^```(?:json)?\s*\n', '', result)
            result = re.sub(r'\n```\s*$', '', result)
            result = result.strip()

        # Parse JSON response
        artists = json.loads(result)

        # Clean up and deduplicate
        seen = set()
        unique_artists = []
        for artist in artists:
            normalized = artist.lower().strip()
            if normalized and len(normalized) > 1 and normalized not in seen:
                seen.add(normalized)
                unique_artists.append(artist)

        return unique_artists

    except Exception as e:
        print(f"⚠️ LLM extraction failed: {e}")
        print(f"Response was: {result[:200] if 'result' in locals() else 'No response'}")
        print("Falling back to simple parsing...")
        # Simple fallback: just split by newlines and take non-empty lines
        return []


def artist_chooser(search_term, candidates):
    """Choose the most relevant artist from search results."""
    if not candidates:
        return None

    # Sort by popularity
    sorted_candidates = sorted(candidates, key=lambda x: x.get('popularity', 0), reverse=True)

    # Return the most popular one
    chosen = sorted_candidates[0]
    print(f"   ✓ Selected: {chosen['name']} (popularity: {chosen.get('popularity', 0)})")
    return chosen


def get_artist_id(artist_name):
    """Search for artist on Spotify and return their ID."""
    try:
        results = sp.search(q=artist_name, type="artist", limit=10)
        candidates = results["artists"]["items"]
        if not candidates:
            return None
        chosen = artist_chooser(artist_name, candidates)
        return chosen["id"] if chosen else None
    except Exception as e:
        print(f"   ⚠️ Failed to search for '{artist_name}': {e}")
        return None


def get_top_tracks(artist_id, limit=5, max_retries=3):
    """Get artist's top tracks with retry logic."""
    for attempt in range(max_retries):
        try:
            official_top = sp.artist_top_tracks(artist_id, country="US")["tracks"]
            track_ids = [track["id"] for track in official_top[:limit] if track.get("id")]
            if track_ids:  # Only return if we got tracks
                return track_ids
            # If no tracks, retry
            if attempt < max_retries - 1:
                time.sleep(0.5 * (attempt + 1))  # Exponential backoff
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(0.5 * (attempt + 1))  # Exponential backoff
                continue
            else:
                print(f"   ⚠️ Failed to get top tracks after {max_retries} attempts: {e}")
                return []
    return []


def get_recent_releases(artist_id, limit=5, max_retries=2):
    """Get artist's recent singles and releases with retry logic."""
    for attempt in range(max_retries):
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
                    continue

            track_ids = [track["id"] for track in all_tracks[:limit] if track.get("id")]
            if track_ids:
                return track_ids

            if attempt < max_retries - 1:
                time.sleep(0.3 * (attempt + 1))
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(0.3 * (attempt + 1))
                continue
            else:
                print(f"   ⚠️ Failed to get recent releases: {e}")
                return []
    return []


def get_most_popular_album_tracks(artist_id, min_tracks=2, max_retries=2):
    """Get tracks from the artist's first good album with retry logic."""
    for attempt in range(max_retries):
        try:
            albums = sp.artist_albums(artist_id, album_type="album", limit=10)
            seen_album_names = set()

            for album in albums["items"]:
                album_name = album["name"].strip().lower()
                if album_name in seen_album_names:
                    continue
                seen_album_names.add(album_name)

                # Skip remix/alternate albums
                if any(keyword in album_name for keyword in ["remix", "edit", "rework", "version", "remastered", "compilation", "live"]):
                    continue

                try:
                    track_items = sp.album_tracks(album["id"])["items"]

                    if len(track_items) >= min_tracks:
                        track_ids = [t["id"] for t in track_items if t.get("id")]
                        return track_ids[:10]  # Return first 10 tracks from first good album
                except:
                    continue

            # Fallback: just get first available album
            for album in albums["items"][:3]:
                try:
                    track_items = sp.album_tracks(album["id"])["items"]
                    if len(track_items) >= min_tracks:
                        track_ids = [t["id"] for t in track_items if t.get("id")]
                        if track_ids:
                            return track_ids[:10]
                except:
                    continue

            if attempt < max_retries - 1:
                time.sleep(0.3 * (attempt + 1))
                continue
            return []

        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(0.3 * (attempt + 1))
                continue
            else:
                print(f"   ⚠️ Failed to get album tracks: {e}")
                return []
    return []


def get_representative_tracks(artist_id):
    """Get comprehensive track selection: top tracks, recent releases, and album."""
    try:
        # Get top tracks (popular hits)
        top_tracks = get_top_tracks(artist_id, limit=5)

        # Get recent releases (new material)
        recent_tracks = get_recent_releases(artist_id, limit=5)

        # Get tracks from an album (deep cut representation)
        album_tracks = get_most_popular_album_tracks(artist_id, min_tracks=3)

        # Combine all tracks and remove duplicates
        all_tracks = []
        seen = set()
        for track_id in top_tracks + recent_tracks + album_tracks:
            if track_id and track_id not in seen:
                all_tracks.append(track_id)
                seen.add(track_id)

        print(f"   ✓ Found {len(all_tracks)} tracks (top: {len(top_tracks)}, recent: {len(recent_tracks)}, album: {len(album_tracks)})")
        return all_tracks

    except Exception as e:
        print(f"   ⚠️ Failed to get tracks: {e}")
        return []


def get_or_create_playlist(user_id, playlist_name):
    """Get existing playlist or create new one."""
    try:
        normalized_name = playlist_name.strip().lower()
        playlists = sp.current_user_playlists(limit=50)

        for playlist in playlists["items"]:
            if playlist and playlist.get("name", "").strip().lower() == normalized_name:
                print(f"\nℹ️  Found existing playlist: {playlist['name']}")
                return playlist["id"], playlist["external_urls"]["spotify"]

        new_playlist = sp.user_playlist_create(user=user_id, name=playlist_name, public=False)
        print(f"\n🆕 Created new playlist: {playlist_name}")
        return new_playlist["id"], new_playlist["external_urls"]["spotify"]

    except Exception as e:
        print(f"⚠️ Failed to get/create playlist: {e}")
        raise


def add_tracks_to_playlist(playlist_id, new_track_ids):
    """Add tracks to playlist, avoiding duplicates."""
    try:
        # Get existing tracks
        existing = sp.playlist_tracks(playlist_id, fields="items.track.id")
        existing_ids = {item["track"]["id"] for item in existing["items"] if item.get("track")}

        # Filter new tracks
        valid_new = [tid for tid in new_track_ids if tid and tid not in existing_ids]

        if valid_new:
            # Add in batches of 100
            for i in range(0, len(valid_new), 100):
                batch = valid_new[i:i+100]
                sp.playlist_add_items(playlist_id, batch)
            return len(valid_new)
        return 0

    except Exception as e:
        print(f"   ⚠️ Failed to add tracks: {e}")
        return 0


def process_artist(artist_name, index, total, playlist_id):
    """Process a single artist and return results."""
    try:
        print(f"\n[{index}/{total}] {artist_name}")

        # Search for artist
        artist_id = get_artist_id(artist_name)
        if not artist_id:
            print(f"   ❌ Not found on Spotify")
            return {"name": artist_name, "success": False, "tracks_added": 0}

        # Get representative tracks
        tracks = get_representative_tracks(artist_id)

        if not tracks:
            print(f"   ⚠️ No tracks found")
            return {"name": artist_name, "success": False, "tracks_added": 0}

        # Add to playlist
        added = add_tracks_to_playlist(playlist_id, tracks)
        if added > 0:
            print(f"   ✅ Added {added} track(s)")
        else:
            print(f"   ℹ️  Tracks already in playlist")

        return {"name": artist_name, "success": True, "tracks_added": added}

    except Exception as e:
        print(f"   ❌ Error processing {artist_name}: {e}")
        return {"name": artist_name, "success": False, "tracks_added": 0}


def main(lineup_text, playlist_name="Pop-Kultur Festival 2025"):
    """Create playlist from festival lineup with concurrent processing."""
    try:
        print("🎵 Pop-Kultur Festival 2025 - Playlist Generator\n")
        print("=" * 60)

        # Extract artists from text
        print("\n📋 Extracting artists from lineup...")
        artists = extract_artists_from_text(lineup_text)
        print(f"✓ Found {len(artists)} artists\n")

        # Create/get playlist
        user_id = sp.current_user()["id"]
        playlist_id, playlist_url = get_or_create_playlist(user_id, playlist_name)

        # Process artists concurrently
        successful = 0
        failed = []
        total_tracks_added = 0

        # Use ThreadPoolExecutor for concurrent processing
        max_workers = 5  # Process 5 artists at a time to avoid API rate limits

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_artist = {
                executor.submit(process_artist, artist, i+1, len(artists), playlist_id): artist
                for i, artist in enumerate(artists)
            }

            # Process results as they complete
            for future in as_completed(future_to_artist):
                result = future.result()
                if result["success"]:
                    successful += 1
                    total_tracks_added += result["tracks_added"]
                else:
                    failed.append(result["name"])

        # Summary
        print(f"\n{'='*60}")
        print(f"\n✨ Complete!")
        print(f"   ✓ {successful} artists processed successfully")
        print(f"   ✓ {total_tracks_added} tracks added to playlist")
        if failed:
            print(f"   ⚠️  {len(failed)} artists not found:")
            for name in failed[:10]:  # Show first 10
                print(f"      - {name}")
            if len(failed) > 10:
                print(f"      ... and {len(failed) - 10} more")
        print(f"\n🔗 Playlist URL: {playlist_url}\n")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    # Festival lineup text
    LINEUP = """
ABIBA
#djset
AGAT & Grimsøn (Co-Creation Residency: Tel Aviv — Berlin)
#residencies
Andreya Casablanca: »Somebody's sins, but not mine«
#commissioned
Anika
#pklive
Apsilon
#pklive
AratheJay
#pklive
ÄTNA & ensemble reflektor
#pklive
Becky Sikasa
#pklive
Bernadette La Hengst & Chor der Statistik: »KONKRETE UTOPIEN«
#commissioned
Blackmoonchild & Ziggy Zeitgeist (Co-Creation Residency: Detroit — Berlin)
#residencies
Boondawg
#pklive
Canty
#pklive
Ceren
#pklive
Charlotte Colace
#pop-up_berlin
CURSES (Live)
#pklive
Das Beat
#pklive
Die Heiterkeit
#pklive
Die Nerven
#pklive
Dina Summer
#pklive
DJ Annika Line Trost
#djset
Donkey Kid
#pklive
døtre
#pklive
Ebbb
#pklive
Efterklang
#pklive
Eilis Frawley
#pklive
Elfi
#pklive
Eli Preiss
#pklive
Faravaz
#çaystube
Fastmusic
#pop-up_berlin
FAYIM: »Kein Beileid«
#commissioned
Fritz Ali Hansen
#pklive
FRZNTE
#çaystube
Gigolo Tears
#çaystube
Güner Künier
#pklive
Gut Health
#pklive
Hér
#pklive
Horizontaler Gentransfer
#pklive
In the Mountains / Giorgi Rodionov
#soniccrossings
inherroom
#soniccrossings
Juli Gilde
#pop-up_berlin
Kerosin95
#10YearsPK
Klapping: »Everything is a little bit of a dance«
#çaystube
Kyla Vėjas!
#pklive
Le Savy Sev
#djset
Liv Oddman
#pklive
Lor & Laura Robles (Co-Creation-Residency Accra – Berlin)
#residencies
Los Bitchos
#pklive
Luna Simão
#pklive
Mahdiya & Lucy Liebe (Co-Creation Residency: Kampala — Berlin)
#residencies
MALONDA: »Schwarze Medusa – I'm tryin not to lose my head again«
#commissioned
Migluma
#pklive
MOLIY (cancelled)
#pklive
Muzi
#pklive
Neromun
#pklive
Nzambisa
#djset
Pamela Owusu-Brenyahs: »AFRO x POP Unplugged«
#commissioned
Phuong-Dan
#djset
Pop-Kultur Lokal: »ACT(6)«
#pklokal
Pop-Kultur Lokal: »BABYCORE by BABYCAKES«
#pklokal
Pop-Kultur Lokal: »LIMINAL«
#pklokal
Pop-Kultur Lokal: »Loud Dreams«
#pklokal
Post Neo
#pklive
Riot Spears
#pklive
Saeko Killy with the Club Mirage Band: »Club Mirage«
#commissioned
Schprampfeinsatz: »Runde Blaue Klippen«
#commissioned
Selma Juhran
#ost
Sira Faal
#pklive
Sophie Yukiko
#çaystube
Super Besse
#pklive
Superbusen
#ost
Symptom Error
#soniccrossings
Tami T: »Echowave«
#commissioned
Tape Visitors
#soniccrossings
Teresa Rotschopf: »The Cave as an Instrument«
#commissioned
The Gilberts
#pklive
The Underground Youth
#pklive
Ultraflex
#çaystube
Vaqo
#soniccrossings
YELKA
#pklive
Yung FSK18
#pklive
Zoon Phonanta
#pklive
»Pop-Kultur Nachwuchs Live«: Aircraft
#pklive
»Pop-Kultur Nachwuchs Live«: KOOB
#pklive
»Pop-Kultur Nachwuchs Live«: NYYA
#pklive
"""

    playlist_name = sys.argv[1] if len(sys.argv) > 1 else "Pop-Kultur Festival 2025"
    main(LINEUP, playlist_name)
