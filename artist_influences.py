#!/usr/bin/env python3
"""
Build a Spotify playlist from an artist's interview-sourced influences.

Reads {artist_slug}_interviews_raw.md, parses the YAML block at the bottom
to extract named influences, then samples each influence artist with the
full sampler approach (top tracks + recent releases + album deep cuts).

Usage:
    python3 artist_influences.py "Rosalía"
    python3 artist_influences.py "Don Toliver" "Kali Uchis" --tracks-per 5
    python3 artist_influences.py "Rosalía" --list-influences
    python3 artist_influences.py "Rosalía" --dry-run
"""

import argparse
import os
import re
import sys
import time
import unicodedata

import requests
import yaml
from dotenv import load_dotenv

load_dotenv()

import spotipy
import spotify_guard  # noqa: F401 — charges every request to the daily budget
from spotipy.oauth2 import SpotifyOAuth

DELAY = 4
BATCH_PAUSE = 30


def make_slug(name):
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", ascii_name.lower()).strip("_")


def parse_interview_yaml(md_path):
    """Extract the YAML block from the interview markdown file."""
    with open(md_path) as f:
        text = f.read()

    m = re.search(r"## Vocabulary Synthesis \(YAML\)\s*```yaml\s*(.+?)```", text, re.DOTALL)
    if not m:
        print(f"  No YAML block found in {md_path}")
        return None
    return yaml.safe_load(m.group(1))


def extract_artist_from_influence_string(s):
    """Extract artist name from strings like 'Bobby Womack — primary influence...'"""
    s = s.strip().strip('"').strip("'")
    for sep in [" — ", " - ", " – "]:
        if sep in s:
            s = s.split(sep, 1)[0]
            break
    return s.strip()


def extract_artist_from_named(s):
    """Extract artist name from strings like 'Kevin Parker (Tame Impala)'"""
    s = s.strip()
    paren = re.search(r"\(([^)]+)\)", s)
    if paren:
        inner = paren.group(1)
        outer = re.sub(r"\s*\([^)]+\)", "", s).strip()
        return [outer, inner] if outer.lower() != inner.lower() else [inner]
    return [s]


SKIP_TERMS = {
    "doo-wop", "soul", "lowrider oldies", "jazz", "punk", "rasta culture",
    "colombian punk/rasta culture", "r&b", "reggaeton", "hip-hop", "trap",
    "disco", "house", "techno", "rock", "pop", "funk", "blues", "gospel",
    "bossa nova", "cumbia", "bolero", "flamenco", "rumba", "salsa",
    "boleros", "boleros (genre)", "the palos themselves",
    "her flamenco mentor (unnamed)", "sonic origin",
}


def extract_influences(yaml_data):
    """Pull all named artist influences from the parsed YAML. Returns deduplicated list."""
    artists = []
    seen = set()

    def add(name):
        key = name.lower().strip()
        if key and key not in seen and key not in SKIP_TERMS and len(key) > 2:
            seen.add(key)
            artists.append(name.strip())

    if not yaml_data:
        return artists

    influence_map = yaml_data.get("influence_map", {})
    for lineage_key, entries in influence_map.items():
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, str):
                    add(extract_artist_from_influence_string(entry))
        elif isinstance(entries, str):
            add(extract_artist_from_influence_string(entries))

    named = yaml_data.get("named_influences", {})
    for category, entries in named.items():
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, str):
                    for name in extract_artist_from_named(entry):
                        add(name)

    for entry in yaml_data.get("subgenre_usage", []):
        if isinstance(entry, dict):
            means = entry.get("means", "")
            quote = entry.get("example_quote", "")
            for text in [means, quote]:
                for pattern in re.findall(r"(?:like |influenced by |inspired by |studying )([A-Z][a-zA-Zé'. ]+?)(?:\s*[-—,;.\n]|$)", text):
                    candidate = pattern.strip().rstrip(".")
                    if 1 < len(candidate.split()) <= 4 and "..." not in candidate:
                        add(candidate)

    return artists


def init_spotify():
    return spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            scope="playlist-modify-public playlist-modify-private",
            cache_path=".cache",
        ),
        requests_session=requests.Session(),
    )


# ---------------------------------------------------------------------------
# Sampler: top tracks + recent releases + album deep cuts (from main.py)
# ---------------------------------------------------------------------------

def _find_artist_id(sp, artist_name):
    """Find the Spotify artist ID via search."""
    try:
        r = sp.search(q=f"artist:{artist_name}", type="artist", limit=5)
        items = r["artists"]["items"]
        if not items:
            return None
        name_lower = artist_name.lower()
        for a in items:
            if a["name"].lower() == name_lower:
                return a["id"]
        for a in items:
            if name_lower in a["name"].lower() or a["name"].lower() in name_lower:
                return a["id"]
        # Exact and substring matches both missed. Returning the top hit here
        # attributes influences to whoever Spotify ranked first; no artist is
        # better than the wrong artist.
        return None
    except Exception:
        return None


def _get_top_tracks(sp, artist_name, artist_id, limit=5):
    """Top tracks via search (artist_top_tracks returns 403 in dev mode)."""
    try:
        r = sp.search(q=f"artist:{artist_name}", type="track", limit=limit + 10)
        items = r["tracks"]["items"]
        matched = [t for t in items if any(a["id"] == artist_id for a in t["artists"])]
        return [(t["id"], t["name"]) for t in matched[:limit]]
    except Exception:
        return []


def _get_recent_releases(sp, artist_id, limit=3):
    """Tracks from recent singles/albums."""
    try:
        all_albums = []
        for off in range(0, 20, 10):
            page = sp.artist_albums(artist_id, limit=10, offset=off)
            all_albums.extend(page["items"])
            if len(page["items"]) < 10:
                break
            time.sleep(1)

        albums = [a for a in all_albums if a.get("album_type") in ("single", "album")]
        tracks = []
        seen_albums = set()

        for album in albums:
            if album["name"] in seen_albums:
                continue
            seen_albums.add(album["name"])
            try:
                at = sp.album_tracks(album["id"])["items"]
                tracks.extend([(t["id"], t["name"]) for t in at if t.get("id")])
                if len(tracks) >= limit:
                    break
            except Exception:
                continue
            time.sleep(1)

        return tracks[:limit]
    except Exception:
        return []


def _get_album_deep_cuts(sp, artist_id, num_albums=1, min_tracks=2):
    """Tracks from top albums for deep cuts."""
    try:
        all_albums = []
        for off in range(0, 30, 10):
            page = sp.artist_albums(artist_id, limit=10, offset=off)
            all_albums.extend(page["items"])
            if len(page["items"]) < 10:
                break
            time.sleep(1)

        seen = set()
        candidates = []
        for a in all_albums:
            if a.get("album_type") not in ("album",):
                continue
            name_lower = a["name"].strip().lower()
            if name_lower in seen:
                continue
            if any(kw in name_lower for kw in ["remix", "edit", "remaster", "live"]):
                continue
            seen.add(name_lower)
            candidates.append(a["id"])

        tracks = []
        albums_used = 0
        for alb_id in candidates[:5]:
            if albums_used >= num_albums:
                break
            try:
                album = sp.album(alb_id)
                items = album.get("tracks", {}).get("items", [])
                if len(items) < min_tracks:
                    continue
                tracks.extend([(t["id"], t["name"]) for t in items if t.get("id")])
                albums_used += 1
            except Exception:
                continue
            time.sleep(1)

        return tracks
    except Exception:
        return []


def sample_artist(sp, artist_name, tracks_per):
    """Full sampler: top tracks + recent releases + album deep cuts."""
    artist_id = _find_artist_id(sp, artist_name)
    if not artist_id:
        return []

    top_count = max(2, tracks_per // 2)
    recent_count = max(1, tracks_per // 3)
    album_count = max(1, tracks_per - top_count - recent_count)

    top = _get_top_tracks(sp, artist_name, artist_id, top_count)
    time.sleep(DELAY)

    recent = _get_recent_releases(sp, artist_id, recent_count)
    time.sleep(DELAY)

    deep = _get_album_deep_cuts(sp, artist_id, num_albums=1) if tracks_per >= 5 else []

    # Merge and dedupe within this artist
    seen = set()
    merged = []
    for tid, name in top + recent + deep:
        if tid not in seen:
            seen.add(tid)
            merged.append((tid, name))

    return merged[:tracks_per]


def get_or_create_playlist(sp, name, description=""):
    offset = 0
    while True:
        page = sp.current_user_playlists(limit=50, offset=offset)
        for pl in page["items"]:
            if pl and pl.get("name", "").strip().lower() == name.strip().lower():
                return pl["id"], pl["external_urls"]["spotify"]
        if len(page["items"]) < 50:
            break
        offset += 50

    token = sp.auth_manager.get_access_token(as_dict=False)
    resp = requests.post(
        "https://api.spotify.com/v1/me/playlists",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"name": name, "public": True, "description": description},
    )
    if resp.status_code == 201:
        pl = resp.json()
        return pl["id"], pl["external_urls"]["spotify"]
    print(f"  Failed to create playlist: {resp.status_code} {resp.text}")
    return None, None


def build_playlist(sp, artist_display, influences, tracks_per, playlist_name=None, dry_run=False):
    name = playlist_name or f"{artist_display}'s Influences (from interviews)"
    desc = f"Sampled from {len(influences)} influence artists mentioned in interviews."

    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"  {len(influences)} influence artists, ~{tracks_per} tracks each (sampler)")
    print(f"{'='*60}")

    if dry_run:
        for a in influences:
            print(f"  [dry-run] would sample: {a}")
        return None, None

    all_tracks = []  # (id, desc_str)
    summary = []
    global_seen = set()  # cross-artist dedup

    for artist in influences:
        print(f"\n  [{artist}]")
        results = sample_artist(sp, artist, tracks_per)
        added = 0
        for tid, tname in results:
            if tid not in global_seen:
                global_seen.add(tid)
                all_tracks.append(tid)
                print(f"    + {tname}")
                added += 1
            else:
                print(f"    ~ {tname} (deduped)")
        if not results:
            print(f"    x not found")
        summary.append((artist, added))

    if not all_tracks:
        print("  No tracks found.")
        return None, None

    print(f"\n--- Creating playlist ({len(all_tracks)} unique tracks) ---")
    pl_id, pl_url = get_or_create_playlist(sp, name, desc)
    if not pl_id:
        return None, None

    for i in range(0, len(all_tracks), 100):
        batch = all_tracks[i : i + 100]
        sp.playlist_add_items(pl_id, [f"spotify:track:{t}" for t in batch])
        print(f"  Added batch of {len(batch)}")
        time.sleep(2)

    print(f"\n  {len(all_tracks)} tracks in playlist")
    print(f"  {pl_url}")

    print(f"\n  Per-artist breakdown:")
    for artist, count in summary:
        marker = "+" * count if count else "x"
        print(f"    {marker:6s} {artist}")

    return pl_id, pl_url


def main():
    ap = argparse.ArgumentParser(description="Build Spotify playlists from interview influences (sampler)")
    ap.add_argument("artists", nargs="+", help="Artist name(s)")
    ap.add_argument("--tracks-per", type=int, default=5, help="Tracks per influence artist (default 5)")
    ap.add_argument("--playlist-name", default=None, help="Override playlist name (single artist only)")
    ap.add_argument("--list-influences", action="store_true", help="Just print influences, don't build playlist")
    ap.add_argument("--dry-run", action="store_true", help="Show what would be searched without calling Spotify")
    args = ap.parse_args()

    sp = None
    if not args.list_influences:
        sp = init_spotify()

    results = []

    for i, artist in enumerate(args.artists):
        slug = make_slug(artist)
        md_path = os.path.join(os.path.dirname(__file__) or ".", f"{slug}_interviews_raw.md")

        print(f"\n--- {artist} ---")
        if not os.path.exists(md_path):
            print(f"  Interview file not found: {md_path}")
            print(f"  Run interview research first to create this file.")
            continue

        yaml_data = parse_interview_yaml(md_path)
        if not yaml_data:
            continue

        influences = extract_influences(yaml_data)
        if not influences:
            print(f"  No influences extracted from YAML.")
            continue

        print(f"  Found {len(influences)} influence artists:")
        for a in influences:
            print(f"    - {a}")

        if args.list_influences:
            continue

        pl_name = args.playlist_name if len(args.artists) == 1 else None
        pl_id, pl_url = build_playlist(sp, artist, influences, args.tracks_per, pl_name, args.dry_run)
        if pl_url:
            results.append((artist, pl_url))

        if i < len(args.artists) - 1 and not args.dry_run and not args.list_influences:
            print(f"\n  --- {BATCH_PAUSE}s cooldown ---")
            time.sleep(BATCH_PAUSE)

    if results:
        print(f"\n{'='*60}")
        print("SUMMARY")
        print(f"{'='*60}")
        for artist, url in results:
            print(f"  {artist}: {url}")


if __name__ == "__main__":
    main()
