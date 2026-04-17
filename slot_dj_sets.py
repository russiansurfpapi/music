"""Pull specific DJ-set playlists from Spotify, classify tracks, and slot into existing sessions.

Usage:
    python3 slot_dj_sets.py --search "xx,nina kraviz,peggy gou"
    python3 slot_dj_sets.py --playlist-id <id>
"""

import argparse
import json
import time
from collections import Counter, defaultdict
from typing import Dict, List, Optional

from spotipy.exceptions import SpotifyException

from auth import get_spotify
from db import connect
from classify import classify_track


def find_playlists(sp, terms: List[str]) -> List[dict]:
    terms_lower = [t.lower().strip() for t in terms]
    found = []
    offset = 0
    while True:
        resp = sp.current_user_playlists(limit=50, offset=offset)
        items = resp.get("items", [])
        if not items:
            break
        for pl in items:
            name = (pl.get("name") or "").lower()
            if any(t in name for t in terms_lower):
                found.append({
                    "id": pl["id"], "name": pl["name"],
                    "total": (pl.get("tracks") or {}).get("total", 0),
                })
        if resp.get("next") is None:
            break
        offset += 50
        time.sleep(0.6)
    return found


def pull_playlist_tracks(sp, pl_id: str) -> List[dict]:
    tracks = []
    offset = 0
    while True:
        try:
            # No fields= filter: dev mode strips fields and the key changed from
            # 'track' to 'item' in newer responses. Take whatever's there.
            resp = sp.playlist_items(
                pl_id, limit=100, offset=offset, additional_types=("track",),
            )
        except SpotifyException as e:
            status = getattr(e, "http_status", None)
            if status == 403:
                print(f"    403 on playlist {pl_id}; skipping")
                return tracks
            if status == 429:
                ra = e.headers.get("Retry-After") if getattr(e, "headers", None) else "?"
                print(f"    429 on playlist {pl_id} (Retry-After: {ra}s) — bailing with {len(tracks)} tracks pulled")
                return tracks
            raise
        items = resp.get("items", [])
        if not items:
            break
        for it in items:
            t = it.get("track") or it.get("item") or {}
            tid = t.get("id")
            # Some dev-mode items return only album/uri without id; derive from uri.
            if not tid and t.get("uri", "").startswith("spotify:track:"):
                tid = t["uri"].split(":")[-1]
            if not tid:
                continue
            artists = t.get("artists") or []
            release = ((t.get("album") or {}).get("release_date") or "")[:4]
            tracks.append({
                "id": tid,
                "title": t.get("name", ""),
                "artist": ", ".join(a["name"] for a in artists) if artists else "",
                "artist_id": artists[0]["id"] if artists else None,
                "year": int(release) if release.isdigit() else None,
            })
        if resp.get("next") is None:
            break
        offset += 100
        time.sleep(0.5)
    return tracks


def classify_external(conn, track: dict) -> dict:
    """Classify a track not necessarily in the DB. Uses stored tags if available."""
    sid = track["id"]
    rows = conn.execute(
        "SELECT tag, count, level FROM track_tags WHERE spotify_id=?", (sid,)
    ).fetchall()
    raw_tags = [{"tag": r["tag"], "count": r["count"], "level": r["level"]} for r in rows]
    artist_genres: List[str] = []
    if track.get("artist_id"):
        gs = conn.execute(
            "SELECT genre FROM artist_genres WHERE artist_id=?", (track["artist_id"],)
        ).fetchall()
        artist_genres = [g["genre"] for g in gs]
    return classify_track(raw_tags, artist_genres, track.get("year"))


def _load_sessions(conn) -> List[dict]:
    rows = conn.execute(
        "SELECT id, kind, name FROM sessions ORDER BY kind, id"
    ).fetchall()
    return [dict(r) for r in rows]


def matching_sessions(cls: dict, track: dict, sessions: List[dict]) -> List[str]:
    """Return session IDs whose criteria this track matches."""
    matches = []
    gen = cls.get("genre")
    sub = cls.get("subgenre")
    dna = cls.get("production_dna") or []
    rhythm = cls.get("rhythm")
    texture = cls.get("texture") or []
    lineage = cls.get("lineage") or []

    for s in sessions:
        sid = s["id"]
        # Focus: subgenre match
        if s["kind"] == "focus":
            # id is "focus:<slug>" — compare name
            target = s["name"].replace("🎧 Study · Focus: ", "").strip()
            if sub and sub.lower() == target.lower():
                matches.append(sid)
        elif s["kind"] == "evolution":
            target = s["name"].replace("🎧 Study · Evolution: ", "").strip()
            if gen and gen.lower() == target.lower():
                matches.append(sid)
        elif s["kind"] == "thread":
            # name: "🎧 Study · Thread: <value> across genres"
            target = s["name"].replace("🎧 Study · Thread: ", "").replace(" across genres", "").strip()
            if target in dna or (rhythm and target == rhythm):
                matches.append(sid)
        elif s["kind"] == "ab":
            # name: "🎧 Study · A/B: <a> vs <b>"
            core = s["name"].replace("🎧 Study · A/B: ", "")
            if " vs " in core:
                a, b = [x.strip() for x in core.split(" vs ", 1)]
                vals = {sub, gen, rhythm, *dna, *texture, *lineage}
                vals = {v for v in vals if v}
                if a in vals or b in vals:
                    matches.append(sid)
    return matches


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--search", help="Comma-separated playlist-name substrings")
    ap.add_argument("--playlist-id")
    ap.add_argument("--cache", default=".cache-library",
                    help="Which Spotify cache to auth against (.cache = curation, .cache-library = premium)")
    args = ap.parse_args()

    sp = get_spotify(cache_name=args.cache)
    me = sp.current_user()
    print(f"Authed as: {me['display_name']} ({me['id']})")
    with connect() as conn:
        sessions = _load_sessions(conn)

        targets: List[dict] = []
        if args.search:
            terms = [t.strip() for t in args.search.split(",") if t.strip()]
            print(f"Searching for playlists matching: {terms}")
            targets = find_playlists(sp, terms)
            print(f"Found {len(targets)}:")
            for t in targets:
                print(f"  {t['name']} ({t['total']} tracks) — {t['id']}")
        elif args.playlist_id:
            pl = sp.playlist(args.playlist_id)
            targets = [{"id": pl["id"], "name": pl["name"],
                        "total": (pl.get("tracks") or {}).get("total", 0)}]
        if not targets:
            print("No playlists found.")
            return

        for pl in targets:
            print(f"\n══ {pl['name']} ({pl['total']} tracks) ══")
            tracks = pull_playlist_tracks(sp, pl["id"])
            print(f"  pulled {len(tracks)} tracks")

            slot_counts: Counter = Counter()
            track_slots: List[dict] = []
            unslotted = 0
            for t in tracks:
                cls = classify_external(conn, t)
                m = matching_sessions(cls, t, sessions)
                if not m:
                    unslotted += 1
                for sid in m:
                    slot_counts[sid] += 1
                track_slots.append({"track": t, "cls": cls, "sessions": m})

            print(f"\n  slots (top 15):")
            for sid, cnt in slot_counts.most_common(15):
                name = next((s["name"] for s in sessions if s["id"] == sid), sid)
                print(f"    {cnt:>4}  {name}")
            print(f"\n  unslotted: {unslotted}")

            # Offer to add: for each session, gather track_ids that matched.
            adds: Dict[str, List[str]] = defaultdict(list)
            for ts in track_slots:
                for sid in ts["sessions"]:
                    adds[sid].append(ts["track"]["id"])

            # Save a per-playlist report under DB for review
            report_path = f"/tmp/slot_{pl['id']}.json"
            with open(report_path, "w") as f:
                json.dump({
                    "playlist": pl, "slots": dict(adds), "unslotted": unslotted,
                }, f, indent=2)
            print(f"  report: {report_path}")


if __name__ == "__main__":
    main()
