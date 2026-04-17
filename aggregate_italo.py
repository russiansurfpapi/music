"""Aggregate italo disco (and adjacent) across user's followed playlists.

Strategy:
  1. Scan all followed playlists for italo-keyword names.
  2. Read tracks from each (owned playlists succeed; followed may 403, we skip those).
  3. For each track: prefer existing classification; else do a lightweight classify
     via Last.fm artist tags cache + the existing classify_track pure function.
  4. Score each track: exact italo-disco classification = 3pts, italo/synth/balearic
     lineage/texture = 2pts, disco-adjacent = 1pt.
  5. Dedupe; sort by score then year; cap at 200.
  6. Push to a new Spotify playlist "🎧 Aggregate · Italo Disco (200)".

Usage:
    python3 aggregate_italo.py build                     # dry-run, save candidates to DB
    python3 aggregate_italo.py push --cap 200            # push to Spotify
"""

import argparse
import json
import os
import re
import time
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from spotipy.exceptions import SpotifyException

from auth import get_spotify
from classify import classify_track
from db import connect

CANDIDATE_KW = [
    "italo", "italodisco", "italian disco", "cosmic disco", "nu-disco", "nu disco",
    "new disco", "balearic", "dark italo", "discoteca", "sexwave", "afro cosmic",
    "disco for cowards", "disco suicide", "ital disco", "80s synth", "synth wave",
    "minimal wave", "winter disco", "french touch", "spaghetti disco",
]

NEGATIVE_KW = ["hip hop", "country", "drill", "metal", "reggaeton", "trap"]


def is_italo_candidate_name(name: str) -> bool:
    n = (name or "").lower()
    if any(k in n for k in NEGATIVE_KW):
        return False
    return any(k in n for k in CANDIDATE_KW)


def fetch_candidates(sp) -> List[dict]:
    playlists = []
    offset = 0
    while True:
        r = sp.current_user_playlists(limit=50, offset=offset)
        items = r.get("items", [])
        if not items:
            break
        for pl in items:
            name = pl.get("name") or ""
            if is_italo_candidate_name(name):
                owner = (pl.get("owner") or {}).get("id", "")
                playlists.append({
                    "id": pl["id"], "name": name, "owner": owner,
                    "is_owned": (owner == sp.current_user()["id"]),
                })
        if r.get("next") is None:
            break
        offset += 50
        time.sleep(0.4)
    return playlists


def pull_tracks(sp, pl_id: str) -> List[dict]:
    tracks: List[dict] = []
    offset = 0
    api_403 = False
    while True:
        try:
            r = sp.playlist_items(pl_id, limit=100, offset=offset,
                                   additional_types=("track",))
        except SpotifyException as e:
            status = getattr(e, "http_status", None)
            if status == 429:
                raise RuntimeError("429")
            if status in (403, 404):
                api_403 = True
                break
            raise
        items = r.get("items", [])
        if not items:
            break
        for it in items:
            t = it.get("track") or it.get("item") or {}
            tid = t.get("id")
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
        if r.get("next") is None or len(items) < 100:
            break
        offset += 100
        time.sleep(0.5)

    if api_403 and not tracks:
        # Public-page fallback: initial HTML contains ~30 embedded track URIs.
        tracks = _pull_public_html(pl_id)
    return tracks


def _pull_public_html(pl_id: str) -> List[dict]:
    """Scrape open.spotify.com/playlist/<id>. Returns ~30 tracks without metadata."""
    try:
        r = requests.get(
            f"https://open.spotify.com/playlist/{pl_id}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
    except requests.RequestException:
        return []
    if r.status_code != 200:
        return []
    ids = []
    seen = set()
    for m in re.finditer(r"spotify:track:([A-Za-z0-9]{22})", r.text):
        tid = m.group(1)
        if tid in seen:
            continue
        seen.add(tid)
        ids.append(tid)
    # We have IDs but no metadata. Return stubs; classifier will look up existing.
    return [{"id": tid, "title": "", "artist": "", "artist_id": None, "year": None}
            for tid in ids]


import requests  # noqa — used by _pull_public_html


def get_classification(conn, sid: str) -> Optional[dict]:
    """Return stored classification if present, else None."""
    r = conn.execute(
        "SELECT genre, subgenre, production_dna, texture, lineage, rhythm, confidence "
        "FROM classifications WHERE spotify_id=?", (sid,)
    ).fetchone()
    if not r:
        return None

    def _list(s):
        try:
            return json.loads(s) if s else []
        except (json.JSONDecodeError, TypeError):
            return []

    return {
        "genre": r["genre"], "subgenre": r["subgenre"], "rhythm": r["rhythm"],
        "production_dna": _list(r["production_dna"]),
        "texture": _list(r["texture"]),
        "lineage": _list(r["lineage"]),
        "confidence": r["confidence"],
    }


def score_track(cls: dict, name_hint: str) -> int:
    """Italo-relevance score. 0 = skip."""
    if not cls:
        return 0
    sub = (cls.get("subgenre") or "").lower()
    gen = (cls.get("genre") or "").lower()
    lin = {x.lower() for x in (cls.get("lineage") or [])}
    tex = {x.lower() for x in (cls.get("texture") or [])}
    dna = {x.lower() for x in (cls.get("production_dna") or [])}
    score = 0
    if sub == "italo disco":
        score += 10
    if sub in ("balearic", "cosmic disco", "disco house", "nu-disco", "french house"):
        score += 3
    if sub in ("synth pop", "electropop", "new wave"):
        score += 2
    if gen == "disco-funk-soul":
        score += 2
    if "synth-driven" in dna:
        score += 1
    if "groovy/funky" in tex or "warm/soulful" in tex:
        score += 1
    if "70s" in lin or "80s" in lin:
        score += 2
    # Boost if the playlist it came from is italo-named.
    if "italo" in name_hint.lower() or "italian" in name_hint.lower():
        score += 2
    return score


def cmd_build(args) -> None:
    sp = get_spotify()
    print(f"Authed: {sp.current_user()['display_name']}")

    candidates = fetch_candidates(sp)
    print(f"Candidate playlists (italo-adjacent names): {len(candidates)}")

    scored: Dict[str, dict] = {}  # spotify_id → best candidate row
    read = 0
    skipped_403 = 0
    with connect() as conn:
        for pl in candidates:
            try:
                tracks = pull_tracks(sp, pl["id"])
            except RuntimeError:
                print(f"⛔ 429 pulling '{pl['name']}' — bailing, saving progress")
                break
            if not tracks:
                skipped_403 += 1
                print(f"  [skip] {pl['name']} (403 or empty)")
                continue
            read += 1
            print(f"  [{read}] {pl['name']}: {len(tracks)} tracks")

            for t in tracks:
                cls = get_classification(conn, t["id"])
                if not cls:
                    # Lightweight classify from existing tags+artist genres.
                    tag_rows = conn.execute(
                        "SELECT tag, count, level FROM track_tags WHERE spotify_id=?",
                        (t["id"],),
                    ).fetchall()
                    raw_tags = [{"tag": r["tag"], "count": r["count"], "level": r["level"]}
                                 for r in tag_rows]
                    a_genres = []
                    if t["artist_id"]:
                        gs = conn.execute(
                            "SELECT genre FROM artist_genres WHERE artist_id=?",
                            (t["artist_id"],),
                        ).fetchall()
                        a_genres = [g["genre"] for g in gs]
                    cls = classify_track(raw_tags, a_genres, t.get("year")) if (raw_tags or a_genres) else None
                score = score_track(cls, pl["name"]) if cls else 0
                # Even without classification: italo-named playlist → minimum score 3
                if score == 0 and ("italo" in pl["name"].lower() or "italian disco" in pl["name"].lower()):
                    score = 3
                if score == 0:
                    continue
                prev = scored.get(t["id"])
                if not prev or score > prev["score"]:
                    scored[t["id"]] = {
                        "id": t["id"], "title": t["title"], "artist": t["artist"],
                        "year": t.get("year"), "score": score,
                        "from": pl["name"],
                        "cls": cls,
                    }
            time.sleep(0.5)

        # Save candidates to a temp table for reuse.
        conn.execute("""CREATE TABLE IF NOT EXISTS aggregate_candidates (
            session_key TEXT, spotify_id TEXT, title TEXT, artist TEXT,
            year INTEGER, score INTEGER, source_playlist TEXT,
            PRIMARY KEY (session_key, spotify_id)
        )""")
        conn.execute("DELETE FROM aggregate_candidates WHERE session_key='italo'")
        for t in scored.values():
            conn.execute(
                "INSERT INTO aggregate_candidates "
                "(session_key, spotify_id, title, artist, year, score, source_playlist) "
                "VALUES ('italo', ?, ?, ?, ?, ?, ?)",
                (t["id"], t["title"], t["artist"], t.get("year"), t["score"], t["from"]),
            )
        conn.commit()

    print(f"\nPlaylists read: {read}")
    print(f"Playlists skipped (403 or empty): {skipped_403}")
    print(f"Unique scored tracks: {len(scored)}")
    strong = [t for t in scored.values() if t["score"] >= 10]
    medium = [t for t in scored.values() if 5 <= t["score"] < 10]
    weak = [t for t in scored.values() if t["score"] < 5]
    print(f"  strong (italo-exact, score ≥10): {len(strong)}")
    print(f"  medium (adjacent, score 5-9):    {len(medium)}")
    print(f"  weak (loose, score <5):          {len(weak)}")


def cmd_push(args) -> None:
    sp = get_spotify()
    uid = sp.current_user()["id"]
    cap = args.cap
    with connect() as conn:
        rows = conn.execute(
            "SELECT spotify_id, title, artist, year, score, source_playlist "
            "FROM aggregate_candidates WHERE session_key='italo' "
            "ORDER BY score DESC, year ASC LIMIT ?",
            (cap,),
        ).fetchall()
        if not rows:
            print("No candidates — run `build` first.")
            return
        ids = [r["spotify_id"] for r in rows]
        from sessions import _create_playlist, _clear_playlist, _replace_playlist_tracks
        name = f"🎧 Aggregate · Italo Disco ({len(ids)})"
        desc = ("Italo disco aggregated across my followed playlists. Ranked by "
                "subgenre match + adjacency (balearic, cosmic, synth-driven, 70s/80s). "
                "Listen for: drum machine + synth bassline; arpeggiated 16ths; warm filtered pads.")

        # Reuse existing aggregate playlist if present.
        existing = conn.execute(
            "SELECT spotify_playlist_id FROM sessions WHERE id='aggregate:italo'"
        ).fetchone()
        if existing and existing[0]:
            pl_id = existing[0]
            print(f"Reusing existing playlist {pl_id} — clearing and replacing.")
            _clear_playlist(sp, pl_id)
        else:
            pl_id = _create_playlist(sp, uid, name, desc)
            print(f"Created playlist {pl_id}")
            conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, kind TEXT, name TEXT, description TEXT,
                track_ids_json TEXT NOT NULL, spotify_playlist_id TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
            conn.execute(
                "INSERT OR REPLACE INTO sessions (id, kind, name, description, "
                "track_ids_json, spotify_playlist_id) VALUES (?, ?, ?, ?, ?, ?)",
                ("aggregate:italo", "aggregate", name, desc, json.dumps(ids), pl_id),
            )
            conn.commit()

        _replace_playlist_tracks(sp, pl_id, ids)
        print(f"Pushed {len(ids)} tracks")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    p = sub.add_parser("push"); p.add_argument("--cap", type=int, default=200)
    args = ap.parse_args()
    if args.cmd == "build":
        cmd_build(args)
    else:
        cmd_push(args)


if __name__ == "__main__":
    main()
