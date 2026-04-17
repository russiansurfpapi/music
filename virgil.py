"""Build and push 3% Chain playlists.

Pulls curated chains from virgil_chains.yaml. For each chain:
  1. Resolve each 'Artist — Title' to a Spotify track ID (DB first, search fallback).
  2. Create/refresh playlist with thesis title + footnotes as description.

Usage:
    python3 virgil.py resolve        # dry-run: resolve tracks, report misses
    python3 virgil.py build          # save resolved sessions to DB (doesn't push)
    python3 virgil.py push [--id X]  # push to Spotify
"""

import argparse
import json
import os
import re
import time
from typing import Dict, List, Optional, Tuple

import yaml
from spotipy.exceptions import SpotifyException

from auth import get_spotify
from db import connect
from sessions import (
    _create_playlist, _clear_playlist, _replace_playlist_tracks, _ensure_schema,
    _upsert_session,
)

CHAINS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "virgil_chains.yaml")
PREFIX = "🎧 3% · "


def _norm(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^\w\s]", "", s)
    return s


def _parse_track(line: str) -> Tuple[str, str]:
    for sep in (" — ", " - ", " – "):
        if sep in line:
            a, t = line.split(sep, 1)
            return a.strip(), t.strip()
    raise ValueError(f"can't parse {line!r} — use 'Artist — Title'")


def _lookup_in_db(conn, artist: str, title: str) -> Optional[str]:
    an, tn = _norm(artist), _norm(title)
    rows = conn.execute(
        "SELECT spotify_id, artist, title FROM tracks WHERE lower(title) LIKE ?",
        (f"%{tn}%",),
    ).fetchall()
    for r in rows:
        if an in _norm(r["artist"]):
            return r["spotify_id"]
    return None


def _spotify_search(sp, artist: str, title: str) -> Optional[str]:
    queries = [
        f"track:{title} artist:{artist}",
        f"{artist} {title}",
    ]
    for q in queries:
        try:
            resp = sp.search(q=q, type="track", limit=5)
        except SpotifyException:
            continue
        items = (resp.get("tracks") or {}).get("items", [])
        an, tn = _norm(artist), _norm(title)
        for it in items:
            it_artist = ", ".join(a["name"] for a in it.get("artists", []))
            if an in _norm(it_artist) and tn in _norm(it["name"]):
                return it["id"]
        if items:  # fall back to top hit on loose match
            it = items[0]
            it_artist = ", ".join(a["name"] for a in it.get("artists", []))
            if an in _norm(it_artist) or tn in _norm(it["name"]):
                return it["id"]
    return None


def _load_chains() -> List[dict]:
    with open(CHAINS_PATH, "r") as f:
        return yaml.safe_load(f)["chains"]


def resolve_chain(conn, sp, chain: dict) -> List[Tuple[str, Optional[str]]]:
    """Returns [(line, track_id_or_None)]. Searches Spotify only for misses."""
    results: List[Tuple[str, Optional[str]]] = []
    for line in chain["tracks"]:
        artist, title = _parse_track(line)
        tid = _lookup_in_db(conn, artist, title)
        if not tid:
            tid = _spotify_search(sp, artist, title)
            time.sleep(0.4)
        results.append((line, tid))
    return results


def cmd_resolve(args) -> None:
    with connect() as conn:
        sp = get_spotify()
        chains = _load_chains()
        for c in chains:
            print(f"\n── {c['title']} ({c['id']}) ──")
            resolved = resolve_chain(conn, sp, c)
            for line, tid in resolved:
                mark = "✓" if tid else "✗"
                print(f"  {mark} {line}")


def cmd_build(args) -> None:
    with connect() as conn:
        _ensure_schema(conn)
        sp = get_spotify()
        chains = _load_chains()
        for c in chains:
            sid = f"chain:{c['id']}"
            resolved = resolve_chain(conn, sp, c)
            ids = [tid for _, tid in resolved if tid]
            misses = [line for line, tid in resolved if not tid]
            name = f"{PREFIX}{c['title']}"
            desc_parts = [c["thesis"].strip(), "", c["footnotes"].strip()]
            if misses:
                desc_parts += ["", "Not found: " + "; ".join(misses)]
            desc = "\n".join(desc_parts)
            _upsert_session(conn, sid, "chain", name, desc, ids)
            print(f"  built {sid}: {len(ids)}/{len(resolved)} tracks"
                  + (f" ({len(misses)} missing)" if misses else ""))
        conn.commit()


def cmd_push(args) -> None:
    with connect() as conn:
        _ensure_schema(conn)
        sp = get_spotify()
        me = sp.current_user()
        uid = me["id"]
        if args.id:
            rows = conn.execute(
                "SELECT id FROM sessions WHERE id=? AND kind='chain'", (args.id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM sessions WHERE kind='chain' ORDER BY id"
            ).fetchall()
        for row in rows:
            sid = row[0]
            s = conn.execute(
                "SELECT name, description, track_ids_json, spotify_playlist_id FROM sessions WHERE id=?",
                (sid,),
            ).fetchone()
            name, desc, ids_json, pl_id = s
            ids = json.loads(ids_json)
            if not ids:
                print(f"  {sid}: no tracks, skip")
                continue
            if not pl_id:
                pl_id = _create_playlist(sp, uid, name, desc or "")
                conn.execute(
                    "UPDATE sessions SET spotify_playlist_id=? WHERE id=?", (pl_id, sid)
                )
                conn.commit()
                print(f"  created: {name} ({pl_id})")
            else:
                _clear_playlist(sp, pl_id)
            _replace_playlist_tracks(sp, pl_id, ids)
            print(f"  pushed: {name} — {len(ids)} tracks")
            time.sleep(1.5)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("resolve")
    sub.add_parser("build")
    p = sub.add_parser("push"); p.add_argument("--id")
    args = ap.parse_args()
    {"resolve": cmd_resolve, "build": cmd_build, "push": cmd_push}[args.cmd](args)


if __name__ == "__main__":
    main()
